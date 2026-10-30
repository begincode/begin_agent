import pytest

from app.core.config import Settings


@pytest.mark.parametrize("env", ["local", "dev", "test", "staging", "prod"])
def test_pms_external_environment_and_explicit_environment_override(tmp_path, monkeypatch, env):
    config = tmp_path / f"application-{env}.yml"
    config.write_text(
        "AGENT_APP_NAME: YAML instance\nAGENT_LOG_LEVEL: WARNING\nAGENT_API_PORT: 8123\n"
        "AGENT_PLATFORM_ADMIN_USER_IDS: '[\"1001760000000000000\"]'\n"
    )
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_ENVIRONMENT", env)
    monkeypatch.setenv("AGENT_APP_NAME", "Environment wins")
    kwargs = (
        {}
        if env in {"local", "test"}
        else dict(
            auth_mode="sso",
            sso_server_url="https://www.example.test/sso",
            sso_client_secret="a" * 40,
            sso_callback_uri="https://www.example.test/agent/sso/callback",
        )
    )
    settings = Settings(_env_file=None, **kwargs)
    assert settings.app_name == "Environment wins" and settings.log_level == "WARNING"
    assert settings.api_port == 8123 and settings.environment == env
    assert settings.platform_admin_user_ids == ["1001760000000000000"]


def test_pms_directories_independent_defaults_and_reject_release_paths(tmp_path, monkeypatch):
    root = tmp_path / "deployment"
    monkeypatch.setenv("PMS_APP_HOME", str(root))
    with pytest.raises(ValueError, match="PMS 目录缺失"):
        Settings(_env_file=None)
    monkeypatch.setenv("PMS_LOG_DIR", str(root / "logs/app"))
    monkeypatch.setenv("PMS_SHARED_DIR", str(root / "shared"))
    monkeypatch.setenv("PMS_RELEASE_DIR", str(root / "releases/rtag1"))
    settings = Settings(_env_file=None)
    assert settings.log_dir == str(root / "logs/app")
    assert settings.credential_key_file == str(root / "shared/data/model-credential.key")
    explicit = Settings(_env_file=None, data_dir=str(tmp_path / "otherdata"))
    assert explicit.log_dir == settings.log_dir
    explicit_log = Settings(_env_file=None, log_dir=str(tmp_path / "otherlog"))
    assert explicit_log.data_dir == settings.data_dir
    with pytest.raises(ValueError, match="版本目录"):
        Settings(_env_file=None, data_dir=str(root / "releases/old/data"))
    with pytest.raises(ValueError, match="绝对路径"):
        Settings(_env_file=None, log_dir="./logs")


def test_production_rejects_local_auth_and_missing_external_configuration(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="非本地环境"):
        Settings(_env_file=None, environment="prod", auth_mode="local")
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(tmp_path / "missing.yml"))
    with pytest.raises((ValueError, FileNotFoundError)):
        Settings(_env_file=None)
