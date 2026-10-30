from app.model_gateway.adapters.openai_compatible import OpenAICompatibleChatAdapter
from app.model_gateway.errors import InvalidModelRequest


class DeepSeekAdapter(OpenAICompatibleChatAdapter):
    def build_payload(self, request, provider, *, stream):
        payload = super().build_payload(request, provider, stream=stream)
        thinking = request.thinking != "disabled"
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if request.tool_choice == "required" and thinking:
            raise InvalidModelRequest("DeepSeek 思考模式不支持 required 工具选择，请关闭思考")
        if request.reasoning_effort is not None:
            if not thinking or request.reasoning_effort not in {"low", "high", "max"}:
                raise InvalidModelRequest("DeepSeek 推理强度仅支持思考模式下的 low/high/max")
        if thinking and request.temperature is not None:
            raise InvalidModelRequest("DeepSeek 思考模式不使用 temperature，请省略或关闭思考")
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def error_code(self, code, status):
        if code in {"context_length_exceeded", "context_window_exceeded"}:
            return "CONTEXT_LENGTH_EXCEEDED"
        if code in {"insufficient_quota", "insufficient_balance"}:
            return "QUOTA_EXCEEDED"
        return super().error_code(code, status)
