import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AGENT_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "Begin Agent"
    environment: str = "local"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "postgresql+asyncpg://begin_agent:begin_agent@localhost:5432/begin_agent"
    model_request_timeout_seconds: float = 120
    credential_encryption_key: SecretStr | None = None
    credential_key_file: str = ".data/model-credential.key"
    model_admin_token: SecretStr | None = None
    auth_mode: Literal["local", "password", "sso"] = "password"
    sso_server_url: str = ""
    sso_client_id: str = "begin-agent"
    sso_client_secret: SecretStr | None = None
    sso_callback_uri: str = ""
    cookie_secure: bool = True
    platform_admin_user_ids: list[str] = Field(default_factory=list)
    log_dir: str | None = None
    data_dir: str | None = None
    worker_database_url: str | None = None
    worker_poll_seconds: float = Field(default=1, ge=0.05, le=60)
    mcp_allowed_hosts: list[str] = Field(default_factory=list)
    worker_lease_seconds: int = Field(default=60, ge=6, le=3600)

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    glm_model: str = "glm-5.3-flash"

    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M3.1-Flash"

    kimi_base_url: str = "https://api.moonshot.cn/v1"
    kimi_model: str = "kimi-k3"

    @field_validator("platform_admin_user_ids", "mcp_allowed_hosts", mode="before")
    @classmethod
    def decode_admin_ids(cls, value):
        # PMS property editors persist values as strings, including JSON list properties.
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                raise ValueError("列表配置必须为 JSON 字符串列表") from None
        return value

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return init_settings, env_settings, external_config, dotenv_settings, file_secret_settings

    @model_validator(mode="after")
    def validate_runtime(self):
        from urllib.parse import urlsplit

        if self.auth_mode == "local" and self.environment not in {"local", "test"}:
            raise ValueError("非本地环境不能使用免登录模式，请使用 AGENT_AUTH_MODE=password")
        if self.auth_mode == "sso":
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.sso_client_id):
                raise ValueError("SSO 应用编码格式无效")
            for value in (self.sso_server_url, self.sso_callback_uri):
                url = urlsplit(value)
                local = url.hostname in {"localhost", "127.0.0.1", "::1"}
                if (url.scheme != "https" and not (local and url.scheme == "http")) or (
                    not url.hostname or url.username or url.password or url.query or url.fragment
                ):
                    raise ValueError("SSO 地址必须为 HTTPS，或本机 HTTP 地址")
            if not self.sso_client_secret or len(self.sso_client_secret.get_secret_value()) < 32:
                raise ValueError("SSO 应用密钥至少 32 字符")
            if not urlsplit(self.sso_callback_uri).path.endswith("/sso/callback"):
                raise ValueError("SSO 回调必须指向应用的 /sso/callback")
            if urlsplit(self.sso_callback_uri).scheme == "https" and not self.cookie_secure:
                raise ValueError("HTTPS 登录必须启用 Secure Cookie")
        managed = bool(os.environ.get("PMS_APP_HOME"))
        self.log_dir = self.log_dir or os.environ.get("PMS_LOG_DIR")
        shared = os.environ.get("PMS_SHARED_DIR")
        self.data_dir = self.data_dir or (str(Path(shared) / "data") if shared else None)
        if managed and (not self.log_dir or not self.data_dir):
            raise ValueError("PMS 目录缺失：需要 PMS_LOG_DIR、PMS_SHARED_DIR 或显式目录配置")
        release = os.environ.get("PMS_RELEASE_DIR")
        for label, value in (("日志", self.log_dir), ("持久数据", self.data_dir)):
            if not value:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise ValueError(f"{label}目录必须为绝对路径：{path}")
            if release and path.resolve().is_relative_to(Path(release).resolve().parent):
                raise ValueError(f"{label}目录不能位于版本目录：{path}")
        if self.data_dir and "credential_key_file" not in self.model_fields_set:
            self.credential_key_file = str(Path(self.data_dir) / "model-credential.key")
        if managed:
            key = Path(self.credential_key_file).expanduser()
            if not key.is_absolute() or (
                release and key.resolve().is_relative_to(Path(release).resolve().parent)
            ):
                raise ValueError(f"PMS 加密主密钥文件必须位于版本目录外的绝对路径：{key}")
        return self


def external_config() -> dict:
    """PMS delivers flat application-<env>.yml properties; no secrets are logged."""
    file = os.environ.get("AGENT_CONFIG_FILE")
    directory = os.environ.get("AGENT_CONFIG_DIR")
    if not file and directory:
        env = os.environ.get("AGENT_ENVIRONMENT", "local")
        if env not in {"local", "dev", "test", "staging", "prod", "production"}:
            raise ValueError("不支持的应用环境")
        file = str(Path(directory) / f"application-{env}.yml")
    if not file:
        return {}
    try:
        data = yaml.safe_load(Path(file).read_text()) or {}
    except yaml.YAMLError:
        raise ValueError("外部配置 YAML 格式错误") from None
    if not isinstance(data, dict):
        raise ValueError("外部配置必须为 YAML 属性对象")
    return {str(k).removeprefix("AGENT_").lower(): v for k, v in data.items()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
