import asyncio
import json

import httpx
import pytest

from app.core.config import Settings
from app.model_gateway.adapters.openai_compatible import StreamState
from app.model_gateway.domain.chat import ChatCompletionRequest, ChatMessage, ProviderCode
from app.model_gateway.errors import InvalidModelRequest, ProviderRequestError
from app.model_gateway.gateway import ADAPTERS, ModelGateway
from app.model_gateway.registry import ProviderConfig

SETTINGS = Settings(_env_file=None)


@pytest.fixture(params=list(ProviderCode), ids=str)
def provider(request):
    code = request.param
    return ProviderConfig(
        code,
        getattr(SETTINGS, f"{code}_base_url"),
        getattr(SETTINGS, f"{code}_model"),
        "test-secret-never-echo",
    )


def request_for(**kwargs):
    return ChatCompletionRequest(
        profile_code="mdl_test", messages=[ChatMessage(role="user", content="你好")], **kwargs
    )


def frame(delta=None, finish=None, **extra):
    return {
        "id": "request-123",
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        **extra,
    }


def sse(*events):
    return (
        ": heartbeat\r\n\r\n"
        + "".join(
            "data: " + (e if isinstance(e, str) else json.dumps(e)) + "\r\n\r\n" for e in events
        )
    ).encode()


@pytest.mark.asyncio
async def test_provider_request_and_full_response(provider):
    def handler(req):
        assert str(req.url) == provider.base_url + "/chat/completions"
        assert req.headers["authorization"] == "Bearer test-secret-never-echo"
        body = json.loads(req.content)
        assert body["model"] == provider.default_model
        assert body["messages"] == [{"role": "user", "content": "你好"}]
        assert body["stream"] is False
        assert body.get("max_completion_tokens", body.get("max_tokens")) == 4096
        if provider.code == ProviderCode.MINIMAX:
            assert body["reasoning_split"] is True
            assert body["thinking"] == {"type": "adaptive"}
            assert "max_tokens" not in body
        if provider.code == ProviderCode.GLM:
            assert body["thinking"]["clear_thinking"] is False
            assert "stream_options" not in body
        if provider.code == ProviderCode.KIMI:
            assert "thinking" not in body
            assert "temperature" not in body
            assert "max_tokens" not in body
        return httpx.Response(
            200,
            json={
                "id": "id-1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "你好",
                            "reasoning_content": "思考内容",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            },
        )

    gw = ModelGateway(SETTINGS, httpx.MockTransport(handler))
    response = await gw.generate(request_for(max_tokens=4096), provider)
    assert response.message.content == "你好"
    assert response.message.reasoning_content == "思考内容"
    assert response.provider_request_id == "id-1"
    assert response.usage["total_tokens"] == 7


@pytest.mark.asyncio
async def test_stream_reasoning_usage_and_replay(provider):
    def handler(req):
        body = json.loads(req.content)
        if provider.code != ProviderCode.GLM:
            assert body["stream_options"]["include_usage"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                frame({"reasoning_content": "思考"}),
                frame({"reasoning_content": "过程"}),
                frame({"content": "你"}),
                frame({"content": "好"}),
                frame(finish="stop"),
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 8,
                        "completion_tokens": 4,
                        "total_tokens": 12,
                        "prompt_tokens_details": {"cached_tokens": 3},
                    },
                },
                "[DONE]",
            ),
        )

    gw = ModelGateway(SETTINGS, httpx.MockTransport(handler))
    events = [e async for e in gw.stream(request_for(), provider)]
    assert "".join(e.delta for e in events if e.type == "content") == "你好"
    result = events[-1].response
    assert events[-1].type == "done" and result.finish_reason == "stop"
    assert result.usage["cached_tokens"] == 3
    replay = ChatCompletionRequest(
        profile_code="mdl_test",
        messages=[
            *request_for().messages,
            result.message,
            ChatMessage(role="user", content="继续"),
        ],
    )
    payload = ADAPTERS[provider.code](5).build_payload(replay, provider, stream=True)
    assert payload["messages"][1]["reasoning_content"] == "思考过程"


TOOL = {
    "type": "function",
    "function": {
        "name": "lookup",
        "description": "lookup",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


@pytest.mark.asyncio
async def test_split_tool_calls_are_assembled_before_exposure(provider):
    def handler(req):
        body = json.loads(req.content)
        assert body["tools"] == [TOOL]
        if provider.code == ProviderCode.GLM:
            assert body["tool_stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                frame(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"city":'},
                            }
                        ]
                    }
                ),
                frame({"tool_calls": [{"index": 0, "function": {"arguments": '"北京"}'}}]}),
                frame(finish="tool_calls"),
                "[DONE]",
            ),
        )

    adapter = ADAPTERS[provider.code](5, httpx.MockTransport(handler))
    events = [e async for e in adapter.stream(request_for(tools=[TOOL]), provider)]
    calls = [e.tool_call for e in events if e.type == "tool_call"]
    assert len(calls) == 1 and json.loads(calls[0].function.arguments) == {"city": "北京"}
    assert events[-1].response.finish_reason == "tool_calls"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [(401, "AUTH_FAILED"), (403, "AUTH_FAILED"), (429, "RATE_LIMITED"), (503, "MODEL_UNAVAILABLE")],
)
async def test_http_error_mapping_does_not_leak_raw_messages(provider, status, expected):
    def handler(req):
        return httpx.Response(
            status, json={"error": {"message": provider.api_key, "code": "opaque"}}
        )

    adapter = ADAPTERS[provider.code](5, httpx.MockTransport(handler))
    with pytest.raises(ProviderRequestError) as caught:
        await adapter.generate(request_for(), provider)
    assert caught.value.code == expected
    assert provider.api_key not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        b"data: {broken}\n\n",
        sse(frame({"content": "partial"})),
        sse("[DONE]"),
        sse({"choices": "wrong"}),
    ],
)
async def test_malformed_or_incomplete_stream_never_reports_done(provider, data):
    adapter = ADAPTERS[provider.code](
        5,
        httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=data
            )
        ),
    )
    events = []
    with pytest.raises(ProviderRequestError):
        async for event in adapter.stream(request_for(), provider):
            events.append(event)
    assert not any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_timeout_and_midstream_error(provider):
    def timeout(req):
        raise httpx.ReadTimeout("test-secret-never-echo")

    adapter = ADAPTERS[provider.code](5, httpx.MockTransport(timeout))
    with pytest.raises(ProviderRequestError) as error:
        await adapter.generate(request_for(), provider)
    assert error.value.code == "TIMEOUT"
    data = sse(
        frame({"content": "partial"}), {"error": {"code": "bad", "message": provider.api_key}}
    )
    adapter = ADAPTERS[provider.code](
        5,
        httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=data
            )
        ),
    )
    with pytest.raises(ProviderRequestError):
        _ = [event async for event in adapter.stream(request_for(), provider)]


class SlowStream(httpx.AsyncByteStream):
    closed = False

    async def __aiter__(self):
        yield sse(frame({"content": "first"}))
        await asyncio.sleep(60)

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_cancel_closes_provider_connection(provider):
    stream = SlowStream()
    adapter = ADAPTERS[provider.code](
        5,
        httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ),
    )
    generator = adapter.stream(request_for(), provider)
    assert (await anext(generator)).type == "content"
    await generator.aclose()
    assert stream.closed


@pytest.mark.parametrize(
    "code,kwargs",
    [
        (ProviderCode.DEEPSEEK, {"tool_choice": "required", "tools": [TOOL]}),
        (ProviderCode.GLM, {"temperature": 1.2}),
        (ProviderCode.GLM, {"thinking": "disabled"}),
        (ProviderCode.MINIMAX, {"thinking": "disabled"}),
        (ProviderCode.MINIMAX, {"temperature": 1}),
        (ProviderCode.KIMI, {"temperature": 0.7}),
        (ProviderCode.KIMI, {"thinking": "disabled"}),
    ],
)
def test_vendor_restrictions_rejected_before_network(code, kwargs):
    config = ProviderConfig(
        code, getattr(SETTINGS, f"{code}_base_url"), getattr(SETTINGS, f"{code}_model"), "x"
    )
    with pytest.raises(InvalidModelRequest):
        ADAPTERS[code](5).build_payload(request_for(**kwargs), config, stream=True)


def test_minimax_authoritative_final_message_does_not_duplicate_text():
    state = StreamState()
    state.consume(frame({"content": "你好", "reasoning_content": "思考"}))
    events = state.consume(
        {
            "choices": [
                {
                    "message": {"content": "你好", "reasoning_content": "思考"},
                    "finish_reason": "stop",
                }
            ],
            "base_resp": {"status_code": 0},
        }
    )
    assert not any(e.type == "content" for e in events)
    assert state.response().message.content == "你好"


@pytest.mark.parametrize(
    "code,body,error",
    [
        (
            ProviderCode.MINIMAX,
            {"base_resp": {"status_code": 1008, "status_msg": "secret"}},
            "QUOTA_EXCEEDED",
        ),
        (ProviderCode.GLM, {"error": {"code": "1113"}}, "QUOTA_EXCEEDED"),
        (ProviderCode.GLM, {"error": {"code": "1301"}}, "CONTENT_FILTERED"),
        (
            ProviderCode.KIMI,
            {"error": {"type": "context_length_exceeded"}},
            "CONTEXT_LENGTH_EXCEEDED",
        ),
    ],
)
def test_business_error_even_when_http_200(code, body, error):
    with pytest.raises(ProviderRequestError) as caught:
        ADAPTERS[code](5).check_error(body)
    assert caught.value.code == error


@pytest.mark.parametrize(
    "reason,error",
    [
        ("sensitive", "CONTENT_FILTERED"),
        ("network_error", "MODEL_UNAVAILABLE"),
        ("model_context_window_exceeded", "CONTEXT_LENGTH_EXCEEDED"),
    ],
)
def test_glm_finish_errors(reason, error):
    state = StreamState()
    state.consume(frame({"content": "partial"}, reason))
    with pytest.raises(ProviderRequestError) as caught:
        state.response()
    assert caught.value.code == error


def test_incomplete_tool_json_is_not_exposed():
    state = StreamState()
    state.consume(
        frame(
            {
                "tool_calls": [
                    {"index": 0, "id": "call", "function": {"name": "lookup", "arguments": "{"}}
                ]
            },
            "tool_calls",
        )
    )
    with pytest.raises(ProviderRequestError):
        state.response()


@pytest.mark.asyncio
async def test_no_redirect_and_no_secret_forwarding(provider):
    count = 0

    def redirect(req):
        nonlocal count
        count += 1
        return httpx.Response(307, headers={"location": "https://attacker.example/steal"})

    adapter = ADAPTERS[provider.code](5, httpx.MockTransport(redirect))
    with pytest.raises(ProviderRequestError) as caught:
        await adapter.generate(request_for(), provider)
    assert count == 1 and caught.value.code == "INVALID_ENDPOINT"


@pytest.mark.asyncio
async def test_total_timeout_terminates_stream_without_retry(provider):
    stream = SlowStream()
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    adapter = ADAPTERS[provider.code](0.02, httpx.MockTransport(handler))
    with pytest.raises(ProviderRequestError) as caught:
        _ = [event async for event in adapter.stream(request_for(), provider)]
    assert caught.value.code == "TIMEOUT" and stream.closed and calls == 1


@pytest.mark.asyncio
async def test_cancellation_during_read_closes_connection(provider):
    stream = SlowStream()
    adapter = ADAPTERS[provider.code](
        5,
        httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ),
    )
    first = asyncio.Event()

    async def collect():
        async for _event in adapter.stream(request_for(), provider):
            first.set()

    task = asyncio.create_task(collect())
    await first.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


def test_content_after_finish_is_not_treated_as_success():
    state = StreamState()
    state.consume(frame({"content": "end"}, "stop"))
    with pytest.raises(ProviderRequestError):
        state.consume(frame({"content": "unexpected extra content"}))


def test_length_preserves_partial_text_without_exposing_partial_tool():
    state = StreamState()
    state.consume(
        frame(
            {
                "content": "partial",
                "tool_calls": [
                    {"index": 0, "id": "call", "function": {"name": "lookup", "arguments": "{"}}
                ],
            },
            "length",
        )
    )
    response = state.response()
    assert response.finish_reason == "length" and response.message.content == "partial"
    assert response.message.tool_calls is None


@pytest.mark.asyncio
async def test_multiline_sse_and_usage_in_finish_chunk(provider):
    chunk = json.dumps(
        frame(
            {"content": "中文🙂"},
            "stop",
            usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        ),
        ensure_ascii=False,
        indent=2,
    )
    body = (
        "event: message\r\n"
        + "\r\n".join("data: " + line for line in chunk.splitlines())
        + "\r\n\r\ndata: [DONE]\r\n\r\n"
    )

    class ByteStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for byte in body.encode():
                yield bytes([byte])

    adapter = ADAPTERS[provider.code](
        5,
        httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=ByteStream()
            )
        ),
    )
    events = [event async for event in adapter.stream(request_for(), provider)]
    assert events[-1].response.message.content == "中文🙂"
    assert events[-1].response.usage["total_tokens"] == 7
