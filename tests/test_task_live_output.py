import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, update
from test_platform_v1 import configured_agent, post, queued

from app.core.identity import LOCAL_TENANT, LOCAL_USER, Principal
from app.model_gateway.domain.chat import ChatMessage, ModelResponse, ModelStreamEvent
from app.model_gateway.gateway import ModelGateway
from app.models.platform import AgentTask
from app.models.workspace import AccountSession
from app.repositories.task_queue import LeaseLost, TaskQueue
from app.runtime.agent import AgentRuntime, redact
from app.runtime.progress import LIMIT, RunProgress
from app.services import task_stream
from app.workers.agent_worker import process_lease

pytest_plugins = ["test_platform_v1"]
PRINCIPAL = Principal(LOCAL_USER, "local", True, LOCAL_TENANT, "TENANT_ADMIN")


class PausedGateway:
    def __init__(self, fail=False):
        self.release, self.started, self.closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.fail = fail

    async def stream(self, request, provider):
        try:
            yield ModelStreamEvent(type="reasoning", delta="先分析问题")
            yield ModelStreamEvent(type="content", delta="中途可见的正文")
            self.started.set()
            await self.release.wait()
            if self.fail:
                return  # No done response is a protocol failure, never success.
            yield ModelStreamEvent(type="content", delta="，完整结束")
            yield ModelStreamEvent(
                type="done",
                response=ModelResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="中途可见的正文，完整结束",
                        reasoning_content="先分析问题",
                    ),
                    finish_reason="stop",
                    usage={"total_tokens": 20},
                ),
            )
        finally:
            self.closed.set()


async def started_job(client, db, gateway, lease_seconds=3):
    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    queue = TaskQueue(db, "live-test", lease_seconds)
    lease = await queue.claim()
    job = asyncio.create_task(process_lease(queue, AgentRuntime(queue, gateway), lease))
    await asyncio.wait_for(gateway.started.wait(), 3)
    for _ in range(100):
        state = await task_stream.snapshot(db, PRINCIPAL, task["task_code"])
        if state["preview"] and state["preview"]["content"]:
            return task, queue, lease, job, state
        await asyncio.sleep(0.02)
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)
    raise AssertionError("No durable preview while upstream is paused")


async def test_stream_preview_precedes_completion_and_reconnect_is_cumulative(
    client, db, monkeypatch
):
    gateway = PausedGateway()
    task, _, _, job, first = await started_job(client, db, gateway)
    try:
        assert first["status"] == "RUNNING" and first["preview"]["content"] == "中途可见的正文"
        detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
        assert detail["output"] is None and detail["artifacts"] == []
        monkeypatch.setattr(task_stream, "CONNECTION_SECONDS", 0.03)
        monkeypatch.setattr(task_stream, "POLL_SECONDS", 0.01)
        stream = await client.get("/api/v1/tasks/" + task["task_code"] + "/stream")
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: snapshot" in stream.text and "event: reconnect" in stream.text
        again = await client.get("/api/v1/tasks/" + task["task_code"] + "/stream")
        assert "中途可见的正文" in again.text and "event: complete" not in again.text
        gateway.release.set()
        await asyncio.wait_for(job, 4)
        final = await client.get("/api/v1/tasks/" + task["task_code"] + "/stream")
        assert "event: complete" in final.text and '"status":"SUCCEEDED"' in final.text
        detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
        assert detail["output"]["text"] == "中途可见的正文，完整结束"
        assert detail["runs"][0]["output"]["live"]["reasoning"] == "先分析问题"
        assert len(detail["artifacts"]) == 1
    finally:
        gateway.release.set()
        await asyncio.gather(job, return_exceptions=True)


async def test_incomplete_stream_keeps_draft_without_successful_context_or_artifact(client, db):
    gateway = PausedGateway(fail=True)
    task, _, _, job, _ = await started_job(client, db, gateway)
    gateway.release.set()
    await asyncio.wait_for(job, 4)
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] == "FAILED" and detail["output"] is None and detail["artifacts"] == []
    assert detail["turns"][0]["output"] is None
    assert detail["runs"][0]["output"]["live"]["content"] == "中途可见的正文"
    assert (await task_stream.snapshot(db, PRINCIPAL, task["task_code"]))["terminal"]


async def test_cancel_closes_upstream_preserves_draft_and_prevents_new_writes(client, db):
    gateway = PausedGateway()
    task, queue, lease, job, state = await started_job(client, db, gateway, 0.3)
    await post(client, "/tasks/" + task["task_code"] + "/cancel", {}, status=200)
    await asyncio.wait_for(job, 3)
    assert gateway.closed.is_set()
    with pytest.raises(LeaseLost):
        await queue.update_live(lease, state["run_code"], {"content": "must-not-commit"})
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] == "CANCELLED" and not detail["artifacts"]
    assert detail["runs"][0]["output"]["live"]["content"] == "中途可见的正文"


async def test_reclaimed_lease_hides_old_attempt_and_fences_old_writer(client, db):
    gateway = PausedGateway()
    task, queue, lease, job, state = await started_job(client, db, gateway)
    try:
        async with db() as session:
            await session.execute(
                update(AgentTask)
                .where(AgentTask.task_code == task["task_code"])
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        reclaimed = await queue.claim()
        assert reclaimed.token != lease.token
        assert (await task_stream.snapshot(db, PRINCIPAL, task["task_code"]))["preview"] is None
        with pytest.raises(LeaseLost):
            await queue.update_live(lease, state["run_code"], {"content": "stale"})
    finally:
        gateway.release.set()
        await asyncio.gather(job, return_exceptions=True)


@pytest.mark.parametrize("revocation", ["session", "role"])
async def test_snapshot_tenant_and_trace_permissions_and_midstream_session_revocation(
    client, db, monkeypatch, revocation
):
    from app.api import accounts, auth

    gateway = PausedGateway()
    task, _, _, job, _ = await started_job(client, db, gateway)
    try:
        role = await post(
            client, "/roles", {"role_name": "仅看任务流", "permissions": ["tasks.read"]}
        )
        user = await post(
            client,
            "/users",
            {
                "username": "streamreader",
                "display_name": "读取者",
                "password": "stream-test-password-2026",
            },
        )
        await post(
            client,
            "/members",
            {
                "user_code": user["user_code"],
                "display_name": "读取者",
                "role_code": role["role_code"],
            },
        )
        reader = Principal(
            user["user_code"],
            user["external_user_id"],
            False,
            LOCAL_TENANT,
            role["role_code"],
            permissions=("tasks.read",),
        )
        state = await task_stream.snapshot(db, reader, task["task_code"])
        assert state["preview"]["content"] and state["activity"] == []
        other = await post(client, "/tenants", {"tenant_name": "任务流隔离租户"})
        assert (
            await client.get(
                "/api/v1/tasks/" + task["task_code"] + "/stream",
                headers={"X-Tenant-Code": other["tenant_code"]},
            )
        ).status_code == 404
        auth.get_settings().auth_mode = "password"
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={"username": "streamreader", "password": "stream-test-password-2026"},
            )
        ).status_code == 200
        monkeypatch.setattr(task_stream, "REAUTH_SECONDS", 0.02)
        monkeypatch.setattr(task_stream, "POLL_SECONDS", 0.01)
        monkeypatch.setattr(task_stream, "CONNECTION_SECONDS", 2)
        assert (await client.get("/api/v1/tasks/" + task["task_code"])).json()["runs"] == []
        assert (await client.get("/api/v1/tasks/" + task["task_code"])).json()["turns"][0][
            "preview"
        ]["content"] == "中途可见的正文"
        reading = asyncio.create_task(client.get("/api/v1/tasks/" + task["task_code"] + "/stream"))
        await asyncio.sleep(0.1)
        async with db() as session:
            if revocation == "session":
                await session.execute(
                    delete(AccountSession).where(AccountSession.user_code == user["user_code"])
                )
            else:
                from app.models.workspace import RoleDefinition

                await session.execute(
                    update(RoleDefinition)
                    .where(RoleDefinition.role_code == role["role_code"])
                    .values(permissions=[])
                )
            await session.commit()
        response = await asyncio.wait_for(reading, 3)
        assert "event: access_error" in response.text
        assert ('"status":401' if revocation == "session" else '"status":403') in response.text
        assert "event: complete" not in response.text
        assert client.cookies.get(accounts.COOKIE)  # Existing HTTP headers are already sent.
    finally:
        gateway.release.set()
        await asyncio.gather(job, return_exceptions=True)


async def test_cross_frame_and_truncated_preview_never_release_credential_prefixes():
    class Queue:
        def __init__(self):
            self.saved = []

        async def update_live(self, lease, run, value):
            self.saved.append(value)

    queue = Queue()
    progress = RunProgress(queue, None, "run", ["runtime-test-secret"], redact)
    await progress.begin_model()
    await progress.add("content", "正文 runtime-test-")
    await progress.flush(force=True)
    assert "runtime" not in queue.saved[-1]["content"]
    await progress.add("content", "secret 结束")
    await progress.flush(force=True)
    assert queue.saved[-1]["content"] == "正文 [REDACTED] 结束"
    response = ModelResponse(
        message=ChatMessage(role="assistant", content="x" * (LIMIT - 4) + "runtime-test-secret"),
        finish_reason="stop",
    )
    await progress.complete(response)
    assert queue.saved[-1]["truncated"] and not queue.saved[-1]["content"].endswith("runt")


async def test_real_gateway_stream_protocol_used_by_runtime(client, db):
    release = asyncio.Event()

    class ProviderStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                "data: "
                + json.dumps(
                    {"choices": [{"delta": {"content": "协议流中途"}, "finish_reason": None}]}
                )
                + "\n\n"
            ).encode()
            await release.wait()
            yield (
                "data: "
                + json.dumps(
                    {
                        "choices": [{"delta": {"content": "完成"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
                    }
                )
                + "\n\ndata: [DONE]\n\n"
            ).encode()

    from app.api import auth

    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    queue = TaskQueue(db, "wire-stream", 3)
    gateway = ModelGateway(
        auth.get_settings(),
        httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, stream=ProviderStream()
            )
        ),
    )
    job = asyncio.create_task(
        process_lease(queue, AgentRuntime(queue, gateway), await queue.claim())
    )
    try:
        for _ in range(100):
            state = await task_stream.snapshot(db, PRINCIPAL, task["task_code"])
            if state["preview"] and state["preview"]["content"]:
                break
            await asyncio.sleep(0.02)
        assert state["preview"]["content"] == "协议流中途" and state["status"] == "RUNNING"
        release.set()
        await asyncio.wait_for(job, 4)
        assert (await client.get("/api/v1/tasks/" + task["task_code"])).json()["output"][
            "text"
        ] == "协议流中途完成"
    finally:
        release.set()
        await asyncio.gather(job, return_exceptions=True)


async def test_tool_phase_visible_and_next_model_call_resets_preview(client, db, monkeypatch):
    from app.model_gateway.domain.chat import FunctionCall, ToolCall
    from app.runtime import agent as runtime_module

    tool = await post(client, "/tools", {"tool_name": "实时工具", "implementation": "text_stats"})
    agent, _ = await configured_agent(client, tools=[tool["tool_code"]])
    task = await queued(client, agent)
    tool_started, tool_release = asyncio.Event(), asyncio.Event()
    second_started, second_release = asyncio.Event(), asyncio.Event()
    original = runtime_module.execute

    async def blocking_tool(name, arguments):
        tool_started.set()
        await tool_release.wait()
        return await original(name, arguments)

    class ToolGateway:
        async def stream(self, request, provider):
            if not any(m.role == "tool" for m in request.messages):
                yield ModelStreamEvent(type="content", delta="工具调用前的草稿")
                yield ModelStreamEvent(
                    type="done",
                    response=ModelResponse(
                        message=ChatMessage(
                            role="assistant",
                            content="工具调用前的草稿",
                            tool_calls=[
                                ToolCall(
                                    id="live_tool",
                                    function=FunctionCall(
                                        name=request.tools[0].function.name,
                                        arguments='{"text":"hello"}',
                                    ),
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                        usage={"total_tokens": 20},
                    ),
                )
            else:
                yield ModelStreamEvent(type="content", delta="工具完成后的新草稿")
                second_started.set()
                await second_release.wait()
                yield ModelStreamEvent(
                    type="done",
                    response=ModelResponse(
                        message=ChatMessage(role="assistant", content="工具完成后的新草稿"),
                        finish_reason="stop",
                        usage={"total_tokens": 20},
                    ),
                )

    monkeypatch.setattr(runtime_module, "execute", blocking_tool)
    queue = TaskQueue(db, "live-tool", 3)
    job = asyncio.create_task(
        process_lease(queue, AgentRuntime(queue, ToolGateway()), await queue.claim())
    )
    try:
        await asyncio.wait_for(tool_started.wait(), 3)
        state = await task_stream.snapshot(db, PRINCIPAL, task["task_code"])
        assert state["preview"]["phase"] == {
            "name": "执行工具",
            "type": "TOOL_CALL",
            "status": "RUNNING",
        }
        tool_release.set()
        await asyncio.wait_for(second_started.wait(), 3)
        state = await task_stream.snapshot(db, PRINCIPAL, task["task_code"])
        assert (
            state["preview"]["call_no"] == 2 and state["preview"]["content"] == "工具完成后的新草稿"
        )
        second_release.set()
        await asyncio.wait_for(job, 3)
    finally:
        tool_release.set()
        second_release.set()
        await asyncio.gather(job, return_exceptions=True)


async def test_child_activity_is_live_but_child_text_is_not_parent_output(client, db):
    child, _ = await configured_agent(client, "实时子 Agent")
    parent, old = await configured_agent(client, "实时主 Agent")
    await post(
        client,
        "/agents/" + parent["agent_code"] + "/relations",
        {"sub_agent_code": child["agent_code"], "call_mode": "SERIAL"},
    )
    await post(
        client,
        "/agents/" + parent["agent_code"] + "/versions",
        {"profile_code": old["profile_code"], "system_prompt": "主 Agent"},
    )
    task = await queued(client, parent)
    gateway = PausedGateway()
    queue = TaskQueue(db, "live-child", 3)
    job = asyncio.create_task(
        process_lease(queue, AgentRuntime(queue, gateway), await queue.claim())
    )
    try:
        await asyncio.wait_for(gateway.started.wait(), 3)
        state = await task_stream.snapshot(db, PRINCIPAL, task["task_code"])
        assert len(state["activity"]) == 2 and state["preview"]["content"] == ""
        assert state["activity"][0]["phase"]["type"] == "AGENT_CALL"
        assert (
            state["activity"][1]["child"] and state["activity"][1]["phase"]["type"] == "MODEL_CALL"
        )
    finally:
        gateway.release.set()
        await asyncio.gather(job, return_exceptions=True)


async def test_timeout_keeps_incomplete_stream_draft_without_final_artifact(client, db):
    agent, _ = await configured_agent(client, execution={"timeout_seconds": 1})
    task = await queued(client, agent)
    gateway = PausedGateway()
    queue = TaskQueue(db, "live-timeout", 3)
    job = asyncio.create_task(
        process_lease(queue, AgentRuntime(queue, gateway), await queue.claim())
    )
    await asyncio.wait_for(job, 4)
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] == "TIMEOUT" and not detail["artifacts"]
    assert detail["turns"][0]["output"] is None and detail["runs"][0]["output"]["live"]["content"]
    assert gateway.closed.is_set()


async def test_historical_failed_draft_survives_new_turn_without_entering_context(client, db):
    from test_platform_v1 import Gateway

    gateway = PausedGateway(fail=True)
    task, queue, _, job, _ = await started_job(client, db, gateway)
    gateway.release.set()
    await asyncio.wait_for(job, 4)
    await post(
        client,
        "/tasks/" + task["task_code"] + "/messages",
        {"prompt": "下一轮要求", "idempotency_key": "history-after-failure"},
    )
    waiting = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert waiting["turns"][0]["preview"]["content"] == "中途可见的正文"
    assert waiting["turns"][0]["output"] is None and waiting["turns"][1]["preview"] is None
    await process_lease(queue, AgentRuntime(queue, Gateway()), await queue.claim())
    final = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert final["turns"][0]["preview"]["content"] == "中途可见的正文"
    assert "中途可见的正文" not in final["output"]["text"]
    assert len(final["artifacts"]) == 1 and final["turns"][1]["status"] == "SUCCEEDED"
