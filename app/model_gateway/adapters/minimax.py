from app.model_gateway.adapters.openai_compatible import OpenAICompatibleChatAdapter
from app.model_gateway.errors import InvalidModelRequest


class MiniMaxAdapter(OpenAICompatibleChatAdapter):
    def build_payload(self, request, provider, *, stream):
        payload = super().build_payload(request, provider, stream=stream)
        model = provider.default_model.lower()
        if request.temperature is not None:
            raise InvalidModelRequest("MiniMax 当前不支持调节 temperature，请省略")
        if request.thinking == "disabled" and model != "minimax-m3":
            raise InvalidModelRequest("当前 MiniMax 模型不能关闭思考")
        payload["thinking"] = {"type": "disabled" if request.thinking == "disabled" else "adaptive"}
        payload["reasoning_split"] = True
        if request.reasoning_effort is not None and model != "minimax-m3.1-flash":
            raise InvalidModelRequest("MiniMax 推理强度配置目前仅适用于 MiniMax-M3.1-Flash")
        if request.max_tokens is not None:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def error_code(self, code, status):
        if code in {"1004", "2049", "2057", "2061", "2068"}:
            return "AUTH_FAILED"
        if code in {"1008", "2053", "2056", "2067"}:
            return "QUOTA_EXCEEDED"
        if code in {"1002", "1039", "1041", "1045", "2062"}:
            return "RATE_LIMITED"
        if code in {"1026", "1027"}:
            return "CONTENT_FILTERED"
        if code in {"2013", "2014", "2022", "2069"}:
            return "INVALID_REQUEST"
        if code in {"1001", "2066"}:
            return "TIMEOUT"
        if code in {"1000", "1024", "1033", "2064"}:
            return "MODEL_UNAVAILABLE"
        return super().error_code(code, status)
