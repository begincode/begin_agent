"""Shared HTTP/SSE machinery. Provider-specific policy lives in providers.py."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import ValidationError

from app.model_gateway.domain.chat import (
    ChatCompletionRequest,
    ChatMessage,
    ModelResponse,
    ModelStreamEvent,
    ToolCall,
)
from app.model_gateway.errors import InvalidModelRequest, ProviderRequestError
from app.model_gateway.registry import ProviderConfig, validate_endpoint


def protocol_error() -> ProviderRequestError:
    return ProviderRequestError("模型服务返回的数据不完整或格式错误", code="INVALID_RESPONSE")


class StreamState:
    def __init__(self) -> None:
        self.content = ""
        self.reasoning = ""
        self.tools: dict[int, dict[str, Any]] = {}
        self.finish: str | None = None
        self.request_id: str | None = None
        self.usage: dict[str, int] | None = None

    def consume(self, payload: dict[str, Any], *, full: bool = False) -> list[ModelStreamEvent]:
        events: list[ModelStreamEvent] = []
        if isinstance(payload.get("id"), str):
            self.request_id = payload["id"][:256]
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            usage = {
                k: v
                for k, v in raw_usage.items()
                if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and type(v) is int
                and v >= 0
            }
            for name, value in [
                ("cached_tokens", raw_usage.get("prompt_cache_hit_tokens")),
                ("cached_tokens", raw_usage.get("cached_tokens")),
                (
                    "cached_tokens",
                    (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                ),
                (
                    "reasoning_tokens",
                    (raw_usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                ),
            ]:
                if type(value) is int and value >= 0:
                    usage[name] = value
            if usage:
                self.usage = usage
                events.append(ModelStreamEvent(type="usage", usage=usage))
        choices = payload.get("choices")
        if choices == [] or choices is None:
            if raw_usage is not None or "base_resp" in payload:
                return events
            raise protocol_error()
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise protocol_error()
        choice = choices[0]
        # MiniMax can send an authoritative full message as its final SSE frame.
        complete = full or "message" in choice
        delta = choice.get("message" if complete else "delta")
        if delta is None:
            delta = {}
        if not isinstance(delta, dict):
            raise protocol_error()
        if (
            self.finish
            and not complete
            and any(delta.get(k) for k in ("content", "reasoning_content", "tool_calls"))
        ):
            raise protocol_error()
        for field, attr, event_type in [
            ("content", "content", "content"),
            ("reasoning_content", "reasoning", "reasoning"),
        ]:
            value = delta.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                raise protocol_error()
            old = getattr(self, attr)
            if complete:
                if not value.startswith(old):
                    raise protocol_error()
                part = value[len(old) :]
                setattr(self, attr, value)
            else:
                part = value
                setattr(self, attr, old + part)
            if part:
                events.append(ModelStreamEvent(type=event_type, delta=part))
        calls = delta.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list) or len(calls) > 128:
                raise protocol_error()
            if complete:
                self.tools = {i: call for i, call in enumerate(calls)}
            else:
                for call in calls:
                    if not isinstance(call, dict):
                        raise protocol_error()
                    index = call.get("index")
                    if type(index) is not int or not 0 <= index < 128:
                        raise protocol_error()
                    acc = self.tools.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if call.get("type", "function") != "function":
                        raise protocol_error()
                    if call.get("id"):
                        if acc["id"] and acc["id"] != call["id"]:
                            raise protocol_error()
                        acc["id"] = call["id"]
                    fn = call.get("function") or {}
                    if not isinstance(fn, dict):
                        raise protocol_error()
                    for field in ("name", "arguments"):
                        if field in fn:
                            if not isinstance(fn[field], str):
                                raise protocol_error()
                            acc["function"][field] += fn[field]
                            if len(acc["function"][field]) > 1_000_000:
                                raise protocol_error()
        reason = choice.get("finish_reason")
        if reason is not None:
            if not isinstance(reason, str) or (self.finish and self.finish != reason):
                raise protocol_error()
            self.finish = reason
        return events

    def response(self) -> ModelResponse:
        reasons = {
            "sensitive": "CONTENT_FILTERED",
            "content_filter": "CONTENT_FILTERED",
            "network_error": "MODEL_UNAVAILABLE",
            "insufficient_system_resource": "MODEL_UNAVAILABLE",
            "model_context_window_exceeded": "CONTEXT_LENGTH_EXCEEDED",
            "aborted": "CANCELLED",
        }
        if self.finish in reasons:
            code = reasons[self.finish]
            raise ProviderRequestError(
                "模型生成未正常完成", code=code, retryable=code == "MODEL_UNAVAILABLE"
            )
        if self.finish not in {"stop", "length", "tool_calls"}:
            raise protocol_error()
        calls: list[ToolCall] = []
        if self.finish == "tool_calls":
            try:
                for index in sorted(self.tools):
                    call = ToolCall.model_validate(self.tools[index])
                    if not isinstance(json.loads(call.function.arguments), dict):
                        raise ValueError
                    calls.append(call)
            except (ValidationError, ValueError, TypeError):
                raise protocol_error() from None
            if not calls or len({call.id for call in calls}) != len(calls):
                raise protocol_error()
        elif self.tools and self.finish != "length":
            raise protocol_error()
        try:
            message = ChatMessage(
                role="assistant",
                content=self.content or None,
                reasoning_content=self.reasoning or None,
                tool_calls=calls or None,
            )
        except ValidationError:
            raise protocol_error() from None
        return ModelResponse(
            message=message,
            finish_reason=self.finish,
            provider_request_id=self.request_id,
            usage=self.usage,
        )


async def sse_events(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    data: list[str] = []
    event = "message"
    size = 0
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield event, "\n".join(data)
            data, event, size = [], "message", 0
        elif line.startswith("data:"):
            part = line[5:].removeprefix(" ")
            size += len(part)
            if size > 2_000_000:
                raise protocol_error()
            data.append(part)
        elif line.startswith("event:"):
            event = line[6:].strip()
    if data:
        yield event, "\n".join(data)


class OpenAICompatibleChatAdapter:
    def __init__(
        self, timeout_seconds: float, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def build_payload(
        self, request: ChatCompletionRequest, provider: ProviderConfig, *, stream: bool
    ) -> dict[str, Any]:
        validate_endpoint(provider.code, provider.base_url)
        payload: dict[str, Any] = {
            "model": provider.default_model,
            "messages": [m.model_dump(exclude_none=True) for m in request.messages],
            "stream": stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = [tool.model_dump() for tool in request.tools]
            payload["tool_choice"] = request.tool_choice
        elif request.tool_choice != "auto":
            raise InvalidModelRequest("未提供工具时不能指定 tool_choice")
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort
        return payload

    def check_error(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = payload.get("error")
        base = payload.get("base_resp")
        if not raw and isinstance(base, dict) and base.get("status_code") not in (None, 0, "0"):
            raw = {"code": base.get("status_code")}
        if status < 400 and not raw:
            return
        code = str(raw.get("code", raw.get("type", ""))) if isinstance(raw, dict) else ""
        normalized = self.error_code(code, status)
        # Never return raw upstream messages: some providers echo secrets or prompts.
        labels = {
            "AUTH_FAILED": "模型 API Key 无效或没有访问权限",
            "QUOTA_EXCEEDED": "模型账户余额或配额不足",
            "RATE_LIMITED": "模型请求受到限流",
            "CONTENT_FILTERED": "模型请求被内容策略拦截",
            "CONTEXT_LENGTH_EXCEEDED": "模型上下文超长",
            "INVALID_REQUEST": "模型请求参数不受支持",
        }
        raise ProviderRequestError(
            labels.get(normalized, "模型服务调用失败"),
            status,
            code=normalized,
            provider_code=code or None,
            retryable=normalized in {"RATE_LIMITED", "MODEL_UNAVAILABLE", "TIMEOUT"},
        )

    def error_code(self, code: str, status: int) -> str:
        if status in {401, 403}:
            return "AUTH_FAILED"
        if status == 402:
            return "QUOTA_EXCEEDED"
        if status == 429:
            return "RATE_LIMITED"
        if status >= 500:
            return "MODEL_UNAVAILABLE"
        if status in {400, 404, 422}:
            return "INVALID_REQUEST"
        return "PROVIDER_ERROR"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=10),
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _json(data: str | bytes) -> dict[str, Any]:
        try:
            value = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise protocol_error() from None
        if not isinstance(value, dict):
            raise protocol_error()
        return value

    async def _check_http(self, response: httpx.Response) -> None:
        if 300 <= response.status_code < 400:
            raise ProviderRequestError("模型接口重定向被拒绝", code="INVALID_ENDPOINT")
        if response.status_code >= 400:
            body = await response.aread()
            try:
                payload = self._json(body)
            except ProviderRequestError:
                payload = {}
            self.check_error(payload, response.status_code)

    async def generate(
        self, request: ChatCompletionRequest, provider: ProviderConfig
    ) -> ModelResponse:
        payload = self.build_payload(request, provider, stream=False)
        try:
            async with asyncio.timeout(self.timeout_seconds), self._client() as client:
                response = await client.post(
                    f"{provider.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {provider.api_key}"},
                )
                await self._check_http(response)
                body = self._json(response.content)
                self.check_error(body)
                state = StreamState()
                state.consume(body, full=True)
                return state.response()
        except (httpx.TimeoutException, TimeoutError):
            raise ProviderRequestError("模型请求超时", code="TIMEOUT", retryable=True) from None
        except httpx.HTTPError:
            raise ProviderRequestError(
                "无法连接模型服务", code="NETWORK_ERROR", retryable=True
            ) from None
        except (KeyError, AttributeError, TypeError):
            raise protocol_error() from None

    async def stream(
        self, request: ChatCompletionRequest, provider: ProviderConfig
    ) -> AsyncIterator[ModelStreamEvent]:
        payload = self.build_payload(request, provider, stream=True)
        state = StreamState()
        try:
            async with asyncio.timeout(self.timeout_seconds), self._client() as client:
                async with client.stream(
                    "POST",
                    f"{provider.base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {provider.api_key}",
                        "Accept": "text/event-stream",
                    },
                ) as response:
                    await self._check_http(response)
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        body = self._json(await response.aread())
                        self.check_error(body)
                        raise protocol_error()
                    async for event_type, data in sse_events(response):
                        if data == "[DONE]":
                            break
                        body = self._json(data)
                        self.check_error(body)
                        if event_type == "error":
                            raise ProviderRequestError("模型流中返回错误", code="PROVIDER_ERROR")
                        for event in state.consume(body):
                            yield event
                    result = state.response()
        except (httpx.TimeoutException, TimeoutError):
            raise ProviderRequestError("模型请求超时", code="TIMEOUT", retryable=True) from None
        except httpx.HTTPError:
            raise ProviderRequestError(
                "模型连接中断", code="NETWORK_ERROR", retryable=True
            ) from None
        except (KeyError, AttributeError, TypeError):
            raise protocol_error() from None
        for call in result.message.tool_calls or []:
            yield ModelStreamEvent(type="tool_call", tool_call=call)
        yield ModelStreamEvent(type="done", response=result)
