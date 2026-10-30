import pytest

from app.core.config import Settings
from app.model_gateway.capabilities import generation_capabilities
from app.model_gateway.domain.chat import ChatCompletionRequest, ChatMessage, ProviderCode
from app.model_gateway.errors import InvalidModelRequest
from app.model_gateway.gateway import ADAPTERS
from app.model_gateway.registry import ProviderConfig


@pytest.mark.parametrize(
    "code,model,can_disable,efforts",
    [
        ("deepseek", "deepseek-flash", True, ["low", "high", "max"]),
        ("glm", "glm-5.3-flash", False, ["low", "high", "max"]),
        ("minimax", "MiniMax-M3.1-Flash", False, ["low", "medium", "high", "xhigh", "max"]),
        ("minimax", "MiniMax-M3", True, []),
        ("kimi", "kimi-k3", False, ["low", "high", "max"]),
        ("kimi", "kimi-k2.7-code", False, []),
        ("kimi", "kimi-k2.6", True, []),
    ],
)
def test_public_controls_agree_with_actual_adapter_validation(code, model, can_disable, efforts):
    code = ProviderCode(code)
    settings = Settings(_env_file=None)
    config = ProviderConfig(code, getattr(settings, f"{code}_base_url"), model, "not-used")
    capabilities = generation_capabilities(code, model)
    assert ("disabled" in capabilities.thinking_modes) is can_disable
    assert capabilities.reasoning_efforts == efforts
    adapter = ADAPTERS[code](5)
    # Every combination advertised to the UI must build a valid upstream request.
    # Unavailable values must fail before any network call, for both delivery modes.
    for streaming in [False, True]:
        for thinking in ["auto", "enabled", "disabled"]:
            for effort in [None, "low", "medium", "high", "xhigh", "max"]:
                request = ChatCompletionRequest(
                    profile_code="test",
                    messages=[ChatMessage(role="user", content="hi")],
                    thinking=thinking,
                    reasoning_effort=effort,
                )
                valid = thinking in capabilities.thinking_modes and (
                    effort is None or (thinking != "disabled" and effort in efforts)
                )
                if valid:
                    payload = adapter.build_payload(request, config, stream=streaming)
                    assert payload["stream"] is streaming
                    if effort:
                        assert payload["reasoning_effort"] == effort
                    else:
                        assert "reasoning_effort" not in payload
                else:
                    with pytest.raises(InvalidModelRequest):
                        adapter.build_payload(request, config, stream=streaming)


def test_unknown_kimi_model_does_not_offer_generation_controls():
    assert generation_capabilities(ProviderCode.KIMI, "unknown").thinking_modes == []
