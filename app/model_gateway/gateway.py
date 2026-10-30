from collections.abc import AsyncIterator

import httpx

from app.core.config import Settings
from app.model_gateway.adapters.deepseek import DeepSeekAdapter
from app.model_gateway.adapters.glm import GLMAdapter
from app.model_gateway.adapters.kimi import KimiAdapter
from app.model_gateway.adapters.minimax import MiniMaxAdapter
from app.model_gateway.domain.chat import (
    ChatCompletionRequest,
    ModelResponse,
    ModelStreamEvent,
    ProviderCode,
)
from app.model_gateway.registry import ProviderConfig, ProviderRegistry

ADAPTERS = {
    ProviderCode.DEEPSEEK: DeepSeekAdapter,
    ProviderCode.GLM: GLMAdapter,
    ProviderCode.MINIMAX: MiniMaxAdapter,
    ProviderCode.KIMI: KimiAdapter,
}


class ModelGateway:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.registry = ProviderRegistry(settings)
        self._adapters = {
            code: cls(settings.model_request_timeout_seconds, transport)
            for code, cls in ADAPTERS.items()
        }

    def validate(self, request: ChatCompletionRequest, provider: ProviderConfig) -> None:
        self._adapters[provider.code].build_payload(request, provider, stream=True)

    async def generate(
        self, request: ChatCompletionRequest, provider: ProviderConfig
    ) -> ModelResponse:
        return await self._adapters[provider.code].generate(request, provider)

    async def stream(
        self, request: ChatCompletionRequest, provider: ProviderConfig
    ) -> AsyncIterator[ModelStreamEvent]:
        self.validate(request, provider)
        yield ModelStreamEvent(type="start")
        upstream = self._adapters[provider.code].stream(request, provider)
        try:
            async for event in upstream:
                yield event
        finally:
            await upstream.aclose()
