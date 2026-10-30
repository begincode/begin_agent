from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.core.config import Settings
from app.model_gateway.domain.chat import ProviderCode, ProviderInfo
from app.model_gateway.errors import InvalidModelRequest


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    code: ProviderCode
    base_url: str
    default_model: str
    api_key: str = field(repr=False)


ALLOWED_HOSTS = {
    ProviderCode.DEEPSEEK: {"api.deepseek.com"},
    ProviderCode.GLM: {"open.bigmodel.cn"},
    ProviderCode.MINIMAX: {"api.minimax.io", "api.minimaxi.com"},
    ProviderCode.KIMI: {"api.moonshot.cn", "api.moonshot.ai"},
}


def validate_endpoint(provider: ProviderCode, base_url: str) -> str:
    url = urlsplit(base_url)
    try:
        valid_port = url.port in (None, 443)
    except ValueError:
        valid_port = False
    if (
        url.scheme != "https"
        or url.hostname not in ALLOWED_HOSTS[provider]
        or not valid_port
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise InvalidModelRequest("模型地址必须使用该供应商的官方 HTTPS 域名")
    paths = {
        ProviderCode.DEEPSEEK: {"", "/v1"},
        ProviderCode.GLM: {"/api/paas/v4", "/api/coding/paas/v4"},
        ProviderCode.MINIMAX: {"/v1"},
        ProviderCode.KIMI: {"/v1"},
    }
    if url.path.rstrip("/") not in paths[provider]:
        raise InvalidModelRequest("模型地址的 API 路径不匹配供应商")
    return base_url.rstrip("/")


class ProviderRegistry:
    """Public provider catalog; credentials live exclusively in model profiles."""

    def __init__(self, settings: Settings) -> None:
        self._providers = [
            ProviderInfo(
                code=code,
                name=name,
                default_model=getattr(settings, f"{code}_model"),
                base_url=getattr(settings, f"{code}_base_url"),
            )
            for code, name in [
                (ProviderCode.DEEPSEEK, "DeepSeek"),
                (ProviderCode.GLM, "智谱 GLM"),
                (ProviderCode.MINIMAX, "MiniMax"),
                (ProviderCode.KIMI, "Kimi"),
            ]
        ]

    def list(self) -> list[ProviderInfo]:
        return self._providers
