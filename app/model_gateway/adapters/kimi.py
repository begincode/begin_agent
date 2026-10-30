from app.model_gateway.adapters.openai_compatible import OpenAICompatibleChatAdapter
from app.model_gateway.errors import InvalidModelRequest


class KimiAdapter(OpenAICompatibleChatAdapter):
    def build_payload(self, request, provider, *, stream):
        payload = super().build_payload(request, provider, stream=stream)
        model = provider.default_model.lower()
        if not (model == "kimi-k3" or model.startswith("kimi-k2.7-code") or model == "kimi-k2.6"):
            raise InvalidModelRequest("当前 Kimi 适配覆盖 K3、K2.7 Code 和 K2.6，请配置对应模型")
        if model != "kimi-k2.6" and request.thinking == "disabled":
            raise InvalidModelRequest("当前 Kimi 模型不可关闭思考")
        expected_temperature = 0.6 if request.thinking == "disabled" else 1.0
        if request.temperature is not None and request.temperature != expected_temperature:
            raise InvalidModelRequest(f"当前 Kimi 模式 temperature 固定为 {expected_temperature}")
        payload.pop("temperature", None)
        if model == "kimi-k3":
            if request.reasoning_effort is not None and request.reasoning_effort not in {
                "low",
                "high",
                "max",
            }:
                raise InvalidModelRequest("Kimi K3 推理强度仅支持 low/high/max")
            # K3 must not receive the K2-specific thinking object.
        else:
            if request.reasoning_effort is not None:
                raise InvalidModelRequest("Kimi K2.x 不支持 reasoning_effort")
            if request.tool_choice == "required":
                raise InvalidModelRequest("Kimi K2.x 不支持 required 工具选择")
            payload["thinking"] = (
                {"type": "disabled"}
                if request.thinking == "disabled"
                else {"type": "enabled", "keep": "all"}
            )
        if request.max_tokens is not None:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def error_code(self, code, status):
        if code in {"insufficient_quota", "insufficient_balance"}:
            return "QUOTA_EXCEEDED"
        if code in {"context_length_exceeded", "exceeded_context_length"}:
            return "CONTEXT_LENGTH_EXCEEDED"
        if code in {"content_filter", "content_filter_error"}:
            return "CONTENT_FILTERED"
        return super().error_code(code, status)
