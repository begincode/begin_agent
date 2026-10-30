"""Public generation controls for the models covered by the provider adapters."""

from typing import Literal

from pydantic import BaseModel

from app.model_gateway.domain.chat import ProviderCode


class GenerationCapabilities(BaseModel):
    thinking_modes: list[Literal["auto", "enabled", "disabled"]]
    reasoning_efforts: list[Literal["low", "medium", "high", "xhigh", "max"]]
    streaming: bool = True
    reasoning_content: bool = True


def generation_capabilities(provider: ProviderCode, model: str) -> GenerationCapabilities:
    model = model.lower()
    modes = ["auto", "enabled"]
    efforts = ["low", "high", "max"]
    if provider == ProviderCode.DEEPSEEK:
        modes.append("disabled")
    elif provider == ProviderCode.GLM:
        if "glm-5.3-flash" not in model:
            modes.append("disabled")
    elif provider == ProviderCode.MINIMAX:
        if model == "minimax-m3":
            modes.append("disabled")
        efforts = ["low", "medium", "high", "xhigh", "max"] if model == "minimax-m3.1-flash" else []
    elif provider == ProviderCode.KIMI:
        if model == "kimi-k2.6":
            modes.append("disabled")
        if model != "kimi-k3":
            efforts = []
        if not (model == "kimi-k3" or model == "kimi-k2.6" or model.startswith("kimi-k2.7-code")):
            modes = []
    return GenerationCapabilities(thinking_modes=modes, reasoning_efforts=efforts)
