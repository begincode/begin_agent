from app.model_gateway.adapters.openai_compatible import OpenAICompatibleChatAdapter
from app.model_gateway.errors import InvalidModelRequest


class GLMAdapter(OpenAICompatibleChatAdapter):
    def build_payload(self, request, provider, *, stream):
        payload = super().build_payload(request, provider, stream=stream)
        if request.max_tokens is not None and request.max_tokens > 131_072:
            raise InvalidModelRequest("当前 GLM 接入的最大输出限制为 131072")
        if request.temperature is not None and (
            request.temperature > 1 or round(request.temperature, 2) != request.temperature
        ):
            raise InvalidModelRequest("GLM temperature 范围为 0～1，最多两位小数")
        if "glm-5.3-flash" in provider.default_model.lower() and request.thinking == "disabled":
            raise InvalidModelRequest("GLM-5.3-Flash 不支持关闭思考")
        payload["thinking"] = {
            "type": "disabled" if request.thinking == "disabled" else "enabled",
            "clear_thinking": False,
        }
        if request.reasoning_effort is not None and (
            request.thinking == "disabled" or request.reasoning_effort not in {"low", "high", "max"}
        ):
            raise InvalidModelRequest("当前 GLM 推理强度仅支持思考模式下的 low/high/max")
        if request.tool_choice not in {"auto", "none"}:
            raise InvalidModelRequest("GLM 当前仅支持 auto 工具选择")
        if request.tool_choice == "none":
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
        if stream and request.tools and request.tool_choice != "none":
            payload["tool_stream"] = True
        # GLM has final-frame usage; do not send an undocumented stream_options field.
        return payload

    def error_code(self, code, status):
        if code in {
            "1113",
            "1308",
            "1309",
            "1310",
            "1314",
            "1316",
            "1317",
            "1318",
            "1319",
            "1320",
            "1321",
        }:
            return "QUOTA_EXCEEDED"
        if code in {"1000", "1001", "1003", "1005", "1220", "1311", "1315"}:
            return "AUTH_FAILED"
        if code == "1301":
            return "CONTENT_FILTERED"
        if code == "1261":
            return "CONTEXT_LENGTH_EXCEEDED"
        if code in {"1302", "1305", "1313"}:
            return "RATE_LIMITED"
        return super().error_code(code, status)
