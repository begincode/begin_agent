import asyncio
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import auth, chat, main
from app.core.config import Settings
from app.core.identity import LOCAL_TENANT, new_code
from app.db.base import Base
from app.model_gateway.domain.chat import ChatMessage, FunctionCall, ModelResponse, ToolCall
from app.models.platform import (
    AgentTask,
    PlatformUser,
    Tenant,
    TenantMember,
)
from app.repositories import platform as repositories
from app.repositories.task_queue import LeaseLost, TaskQueue
from app.runtime.agent import AgentRuntime
from app.workers.agent_worker import process_lease


@pytest_asyncio.fixture
async def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated PostgreSQL TEST_DATABASE_URL")
    schema = "platform_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture
async def client(db, tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None, auth_mode="local", credential_key_file=str(tmp_path / "key")
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    from app.api import accounts, workspace
    from app.tools import mcp

    for module in (auth, chat, main, accounts, workspace, mcp):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    for module in (auth, chat, repositories):
        monkeypatch.setattr(module, "session_factory", db)
    monkeypatch.setattr("app.runtime.agent.get_settings", lambda: settings)
    from app.model_gateway.gateway import ModelGateway

    monkeypatch.setattr(chat, "get_model_gateway", lambda: ModelGateway(settings))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.create_app(), client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as c:
        assert (await c.get("/api/v1/me")).status_code == 200
        yield c


async def post(client, path, data, status=201):
    response = await client.post("/api/v1" + path, json=data)
    assert response.status_code == status, response.text
    return response.json()


async def configured_agent(client, name="助手", tools=None, execution=None):
    profile = await post(
        client,
        "/model-profiles",
        {
            "provider_code": "deepseek",
            "display_name": "测试模型",
            "model_code": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
            "api_key": "runtime-test-secret",
        },
    )
    agent = await post(client, "/agents", {"agent_name": name})
    version = await post(
        client,
        f"/agents/{agent['agent_code']}/versions",
        {
            "profile_code": profile["profile_code"],
            "system_prompt": name,
            "tool_codes": tools or [],
            "execution_config": execution or {},
        },
    )
    return agent, version


async def queued(client, agent):
    return await post(
        client,
        "/tasks",
        {"agent_code": agent["agent_code"], "title": "验收", "prompt": "请完成任务"},
    )


@pytest.mark.asyncio
async def test_versions_relations_idempotency_and_tenant_scope(client, db):
    child, child_version = await configured_agent(client, "子助手")
    master, old = await configured_agent(client, "主助手")
    a, b = master["agent_code"], child["agent_code"]
    await post(client, f"/agents/{a}/relations", {"sub_agent_code": b, "call_mode": "PARALLEL"})
    await post(client, f"/agents/{b}/relations", {"sub_agent_code": a}, status=409)
    version = await post(
        client,
        f"/agents/{a}/versions",
        {"profile_code": old["profile_code"], "system_prompt": "已发布提示"},
    )
    assert version["version_no"] == 2
    assert (
        version["dependency_snapshot"]["children"][0]["version_code"]
        == child_version["version_code"]
    )
    assert not old["dependency_snapshot"]["children"]
    edited = await client.patch(
        "/api/v1/model-profiles/" + old["profile_code"], json={"model_code": "changed"}
    )
    assert edited.status_code == 200
    versions = (await client.get(f"/api/v1/agents/{a}/versions")).json()
    assert all(v["model_snapshot"]["model_code"] == "deepseek-flash" for v in versions)
    data = {"agent_code": a, "title": "幂等", "prompt": "输入", "idempotency_key": "same"}
    first, second = await asyncio.gather(post(client, "/tasks", data), post(client, "/tasks", data))
    assert first["task_code"] == second["task_code"]
    await post(client, "/tasks", {**data, "prompt": "不同"}, status=409)
    assert "id" not in first and "lease_token" not in first
    tenant = await post(client, "/tenants", {"tenant_name": "另一个租户"})
    response = await client.get(
        f"/api/v1/agents/{a}/versions", headers={"X-Tenant-Code": tenant["tenant_code"]}
    )
    assert response.status_code == 404
    assert (
        await client.get("/api/v1/model-profiles", headers={"X-Tenant-Code": tenant["tenant_code"]})
    ).json() == []
    async with db() as session:
        saved = await session.scalar(
            select(AgentTask).where(AgentTask.task_code == first["task_code"])
        )
        assert saved.agent_version_code == version["version_code"]


@pytest.mark.asyncio
async def test_concurrent_queue_claim_expiry_fencing_and_cancel(client, db):
    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    q1, q2 = TaskQueue(db, "one", 6), TaskQueue(db, "two", 6)
    results = await asyncio.gather(q1.claim(), q2.claim())
    assert sum(r is not None for r in results) == 1
    first = next(r for r in results if r)
    async with db() as session:
        await session.execute(
            update(AgentTask)
            .where(AgentTask.task_code == first.task_code)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    second = await q2.claim()
    assert second and second.token != first.token
    for action in (q1.heartbeat(first), q1.finish(first, "SUCCEEDED", {"text": "stale"})):
        with pytest.raises(LeaseLost):
            await action
    cancelled = await post(client, f"/tasks/{task['task_code']}/cancel", {}, status=200)
    assert cancelled["status"] == "CANCELLED"
    with pytest.raises(LeaseLost):
        await q2.finish(second, "SUCCEEDED")


@pytest.mark.asyncio
async def test_disabled_tenant_retains_history_but_blocks_new_work(client):
    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    response = await client.patch(
        "/api/v1/tenants/" + LOCAL_TENANT, json={"tenant_name": "停用租户", "status": 0}
    )
    assert response.status_code == 200
    headers = {"X-Tenant-Code": LOCAL_TENANT}
    assert (
        await client.get("/api/v1/tasks/" + task["task_code"], headers=headers)
    ).status_code == 200
    assert (await client.get("/api/v1/agents", headers=headers)).status_code == 200
    identity = (await client.get("/api/v1/me", headers=headers)).json()
    assert identity["tenants"][0]["status"] == 0
    denied = await client.post(
        "/api/v1/tasks",
        json={"agent_code": agent["agent_code"], "title": "不允许", "prompt": "停止"},
        headers=headers,
    )
    assert denied.status_code == 403 and "租户已停用" in denied.text
    assert (await client.post("/api/v1/chat", json={}, headers=headers)).status_code == 403
    enabled = await client.patch(
        "/api/v1/tenants/" + LOCAL_TENANT,
        json={"tenant_name": "重新启用", "status": 1},
        headers=headers,
    )
    assert enabled.status_code == 200


class Gateway:
    def __init__(self, tool=False, block=False):
        self.tool = tool
        self.block = block
        self.started, self.cancelled = asyncio.Event(), asyncio.Event()

    async def generate(self, request, provider):
        self.started.set()
        if self.block:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()
        if self.tool and not any(m.role == "tool" for m in request.messages):
            message = ChatMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="tool1",
                        function=FunctionCall(
                            name=request.tools[0].function.name, arguments='{"text":"hello world"}'
                        ),
                    )
                ],
            )
            return ModelResponse(
                message=message, finish_reason="tool_calls", usage={"total_tokens": 20}
            )
        return ModelResponse(
            message=ChatMessage(role="assistant", content="完成 runtime-test-secret"),
            finish_reason="stop",
            usage={"total_tokens": 20},
        )


@pytest.mark.asyncio
async def test_runtime_tool_steps_messages_artifact_and_secret_redaction(client, db):
    tool = await post(client, "/tools", {"tool_name": "字数统计", "implementation": "text_stats"})
    agent, version = await configured_agent(client, tools=[tool["tool_code"]])
    task = await queued(client, agent)
    queue = TaskQueue(db, "test", 6)
    lease = await queue.claim()
    await process_lease(queue, AgentRuntime(queue, Gateway(tool=True)), lease)
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] == "SUCCEEDED", detail
    assert detail["runs"][0]["agent_version_code"] == version["version_code"]
    assert detail["artifacts"][0]["content"]["text"] == "完成 [REDACTED]"
    run = (await client.get("/api/v1/runs/" + detail["runs"][0]["run_code"])).json()
    assert [s["step_type"] for s in run["steps"]] == [
        "CONTEXT_BUILD",
        "MODEL_CALL",
        "TOOL_CALL",
        "MODEL_CALL",
        "RESULT_PROCESS",
    ]
    assert [m["role"] for m in run["messages"]] == [
        "SYSTEM",
        "USER",
        "ASSISTANT",
        "TOOL",
        "ASSISTANT",
    ]
    assert "runtime-test-secret" not in json.dumps([detail, run])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["SERIAL", "PARALLEL", "ON_DEMAND"])
async def test_subagent_tree_uses_published_child_versions(client, db, mode):
    child, cv = await configured_agent(client, "子助手")
    master, mv = await configured_agent(client, "主助手")
    await post(
        client,
        f"/agents/{master['agent_code']}/relations",
        {"sub_agent_code": child["agent_code"], "call_mode": mode},
    )
    await post(
        client,
        f"/agents/{master['agent_code']}/versions",
        {"profile_code": mv["profile_code"], "system_prompt": "主助手"},
    )
    await post(
        client,
        f"/agents/{child['agent_code']}/versions",
        {"profile_code": cv["profile_code"], "system_prompt": "新子版本不应该被运行"},
    )

    class Delegating(Gateway):
        async def generate(self, request, provider):
            if (
                mode == "ON_DEMAND"
                and request.tools
                and not any(m.role == "tool" for m in request.messages)
            ):
                return ModelResponse(
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=[
                            ToolCall(
                                id="sub1",
                                function=FunctionCall(
                                    name=request.tools[0].function.name,
                                    arguments='{"prompt":"子任务"}',
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                    usage={"total_tokens": 20},
                )
            return await super().generate(request, provider)

    task = await queued(client, master)
    queue = TaskQueue(db, "tree", 6)
    await process_lease(queue, AgentRuntime(queue, Delegating()), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] == "SUCCEEDED", detail
    root, sub = detail["runs"]
    assert sub["agent_version_code"] == cv["version_code"]
    assert sub["parent_run_code"] == root["run_code"] and sub["root_run_code"] == root["run_code"]
    assert sub["parent_step_code"] and sub["run_depth"] == 1
    disabled = await client.patch(
        "/api/v1/agents/" + child["agent_code"],
        json={"agent_name": "子助手", "agent_type": "NORMAL", "status": 0},
    )
    assert disabled.status_code == 200
    later = await queued(client, master)
    await process_lease(queue, AgentRuntime(queue, Delegating()), await queue.claim())
    stopped = (await client.get("/api/v1/tasks/" + later["task_code"])).json()
    assert stopped["status"] == "FAILED" and stopped["error_code"] == "CONFIG_DISABLED"
    assert not stopped["artifacts"]


@pytest.mark.asyncio
async def test_cancellation_stops_blocked_upstream_and_cannot_commit(client, db):
    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    queue = TaskQueue(db, "blocked", 6)
    gateway = Gateway(block=True)
    job = asyncio.create_task(
        process_lease(queue, AgentRuntime(queue, gateway), await queue.claim())
    )
    await asyncio.wait_for(gateway.started.wait(), 3)
    await post(client, f"/tasks/{task['task_code']}/cancel", {}, status=200)
    await asyncio.wait_for(job, 4)
    assert gateway.cancelled.is_set()
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] == "CANCELLED" and not detail["artifacts"]
    assert all(r["status"] == "CANCELLED" for r in detail["runs"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits,reason",
    [({"max_depth": 0}, "MAX_DEPTH"), ({"max_sub_agent_calls": 0}, "BUDGET_EXCEEDED")],
)
async def test_child_call_limits_stop_before_any_model_request(client, db, limits, reason):
    child, _ = await configured_agent(client, "子助手")
    master, version = await configured_agent(client, "主助手")
    await post(
        client,
        f"/agents/{master['agent_code']}/relations",
        {"sub_agent_code": child["agent_code"], "call_mode": "SERIAL"},
    )
    await post(
        client,
        f"/agents/{master['agent_code']}/versions",
        {
            "profile_code": version["profile_code"],
            "system_prompt": "主助手",
            "execution_config": limits,
        },
    )
    task = await queued(client, master)
    queue, gateway = TaskQueue(db, "limits", 6), Gateway()
    await process_lease(queue, AgentRuntime(queue, gateway), await queue.claim())
    result = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert result["error_code"] == reason and not gateway.started.is_set()
    assert not result["artifacts"]


@pytest.mark.asyncio
async def test_two_parallel_children_reach_the_model_concurrently(client, db):
    a, _ = await configured_agent(client, "parallel-A")
    b, _ = await configured_agent(client, "parallel-B")
    master, version = await configured_agent(client, "parallel-parent")
    for child in (a, b):
        await post(
            client,
            f"/agents/{master['agent_code']}/relations",
            {"sub_agent_code": child["agent_code"], "call_mode": "PARALLEL"},
        )
    await post(
        client,
        f"/agents/{master['agent_code']}/versions",
        {"profile_code": version["profile_code"], "system_prompt": "parallel-parent"},
    )

    class Concurrent(Gateway):
        def __init__(self):
            super().__init__()
            self.arrivals = set()
            self.barrier = asyncio.Event()

        async def generate(self, request, provider):
            name = request.messages[0].content
            if name in {"parallel-A", "parallel-B"}:
                self.arrivals.add(name)
                if len(self.arrivals) == 2:
                    self.barrier.set()
                await self.barrier.wait()
            return await super().generate(request, provider)

    task = await queued(client, master)
    queue, gateway = TaskQueue(db, "parallel", 6), Concurrent()
    await asyncio.wait_for(
        process_lease(queue, AgentRuntime(queue, gateway), await queue.claim()), 5
    )
    result = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert result["status"] == "SUCCEEDED" and len(result["runs"]) == 3
    assert len(gateway.arrivals) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config,expected", [({"max_tokens": 1}, "BUDGET_EXCEEDED"), ({"timeout_seconds": 1}, "TIMEOUT")]
)
async def test_runtime_budget_and_timeout(client, db, config, expected):
    agent, _ = await configured_agent(client, execution=config)
    task = await queued(client, agent)
    queue = TaskQueue(db, "limits", 6)
    await process_lease(
        queue, AgentRuntime(queue, Gateway(block=expected == "TIMEOUT")), await queue.claim()
    )
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["error_code"] == expected
    assert not detail["artifacts"]


@pytest.mark.asyncio
async def test_transient_provider_failures_backoff_then_exhaust_retry_budget(client, db):
    from app.model_gateway.errors import ProviderRequestError

    class Unavailable(Gateway):
        async def generate(self, request, provider):
            raise ProviderRequestError("must-not-echo-secret", 503, retryable=True)

    agent, _ = await configured_agent(client)
    task = await post(
        client,
        "/tasks",
        {"agent_code": agent["agent_code"], "title": "retry", "prompt": "hello", "max_retries": 1},
    )
    queue = TaskQueue(db, "retry", 6)
    await process_lease(queue, AgentRuntime(queue, Unavailable()), await queue.claim())
    assert await queue.claim() is None  # Backoff must prevent immediate hot-loop retries.
    async with db() as session:
        await session.execute(
            update(AgentTask)
            .where(AgentTask.task_code == task["task_code"])
            .values(next_retry_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    await process_lease(queue, AgentRuntime(queue, Unavailable()), await queue.claim())
    response = await client.get("/api/v1/tasks/" + task["task_code"])
    detail = response.json()
    assert detail["status"] == "FAILED" and detail["retry_count"] == 1
    assert all(r["status"] == "FAILED" for r in detail["runs"])
    assert "must-not-echo-secret" not in response.text


@pytest.mark.asyncio
async def test_max_steps_and_unauthorized_tools_cannot_produce_success(client, db):
    tool = await post(client, "/tools", {"tool_name": "stats", "implementation": "text_stats"})
    agent, _ = await configured_agent(client, tools=[tool["tool_code"]], execution={"max_steps": 1})
    task = await queued(client, agent)
    queue = TaskQueue(db, "steps", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway(tool=True)), await queue.claim())
    assert (await client.get("/api/v1/tasks/" + task["task_code"])).json()[
        "error_code"
    ] == "MAX_STEPS"

    class Unauthorized(Gateway):
        async def generate(self, request, provider):
            return ModelResponse(
                message=ChatMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id="bad",
                            function=FunctionCall(name="shell", arguments='{"command":"echo bad"}'),
                        )
                    ],
                ),
                finish_reason="tool_calls",
                usage={"total_tokens": 20},
            )

    another = await queued(client, agent)
    await process_lease(queue, AgentRuntime(queue, Unauthorized()), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + another["task_code"])).json()
    assert detail["error_code"] == "TOOL_FAILED" and not detail["artifacts"]


def sso_settings():
    return Settings(
        _env_file=None,
        auth_mode="sso",
        sso_server_url="https://sso.example.test/sso",
        sso_callback_uri="https://app.example.test/agent/sso/callback",
        sso_client_secret="a" * 40,
    )


@pytest.mark.asyncio
async def test_sso_pkce_callback_cookie_and_no_exchange_retry(monkeypatch):
    settings = sso_settings()
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    seen = []

    def upstream(request):
        seen.append(request)
        assert request.headers["authorization"].startswith("Basic ")
        assert request.headers["x-sso-client-authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "token": "sess_" + "t" * 43,
                "userId": "1001760000000000000",
                "expireTime": int(time.time() * 1000) + 100000,
            },
        )

    monkeypatch.setattr(
        auth, "get_sso_remote", lambda: auth.SsoRemote(settings, httpx.MockTransport(upstream))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.create_app(), root_path="/agent"),
        base_url="https://app.example.test",
    ) as c:
        start = await c.get("/agent/sso/login?returnTo=/agent/")
        assert start.status_code == 303
        params = parse_qs(urlsplit(start.headers["location"]).query)
        assert params["clientId"] == ["begin-agent"] and "ui" not in params
        state = params["state"][0]
        flow = json.loads(
            auth.flow_cipher().decrypt(c.cookies.get(auth.COOKIE + "_flow_" + state).encode())
        )
        import base64

        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert params["challenge"] == [challenge]
        callback = f"/agent/sso/callback?state={state}&handoff=handoff_{'h' * 43}"
        result = await c.get(callback)
        assert result.status_code == 303 and result.headers["location"] == "/agent/"
        assert "sess_" not in result.headers["location"]
        assert (
            "HttpOnly" in result.headers["set-cookie"] and "Secure" in result.headers["set-cookie"]
        )
        assert len(seen) == 1 and json.loads(seen[0].content)["verifier"] == flow["verifier"]
        replay = await c.get(callback)
        assert replay.status_code == 400 and len(seen) == 1
        assert replay.headers["referrer-policy"] == "no-referrer"
        assert (await c.get("/agent/sso/login?returnTo=//evil.test")).status_code == 400
        assert (await c.get("/agent/sso/login?returnTo=/%2f%2fevil.test")).status_code == 400


@pytest.mark.asyncio
async def test_sso_roles_tenant_headers_csrf_failure_distinction_and_logout(db, monkeypatch):
    s = sso_settings()
    user_code, tenant = new_code("usr"), new_code("ten")
    async with db() as session:
        session.add_all(
            [
                PlatformUser(
                    user_code=user_code, external_user_id="1001760000000000000", display_name="用户"
                ),
                Tenant(tenant_code=tenant, tenant_name="租户"),
                TenantMember(
                    tenant_code=tenant,
                    member_code=new_code("mem"),
                    user_code=user_code,
                    role_code="MEMBER",
                ),
            ]
        )
        await session.commit()
    for module in (auth, main):
        monkeypatch.setattr(module, "get_settings", lambda: s)
    for module in (auth, repositories):
        monkeypatch.setattr(module, "session_factory", db)
    status = 200
    paths = []

    def upstream(request):
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer sess_" + "t" * 43
        assert request.headers["x-sso-client-authorization"].startswith("Basic ")
        return httpx.Response(status, json={"userId": "1001760000000000000", "loggedOut": True})

    monkeypatch.setattr(
        auth, "get_sso_remote", lambda: auth.SsoRemote(s, httpx.MockTransport(upstream))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.create_app()), base_url="https://app.example.test"
    ) as c:
        c.cookies.set(auth.COOKIE, "sess_" + "t" * 43, domain="app.example.test", path="/")
        assert (await c.get("/api/v1/me")).json()["external_user_id"] == "1001760000000000000"
        assert (await c.post("/api/v1/agents", json={"agent_name": "forbidden"})).status_code == 403
        assert (
            await c.post(
                "/api/v1/agents",
                json={"agent_name": "forbidden"},
                headers={"Origin": "https://app.example.test"},
            )
        ).status_code == 403
        assert (
            await c.get("/api/v1/me", headers={"X-Tenant-Code": LOCAL_TENANT})
        ).status_code == 403
        async with db() as session:
            await session.execute(
                update(Tenant).where(Tenant.tenant_code == tenant).values(status=0)
            )
            await session.commit()
        assert (await c.get("/api/v1/tasks")).status_code == 200
        stopped = await c.post(
            "/api/v1/tasks",
            json={"agent_code": "missing", "title": "blocked", "prompt": "blocked"},
            headers={"Origin": "https://app.example.test"},
        )
        assert stopped.status_code == 403 and "租户已停用" in stopped.text
        async with db() as session:
            await session.execute(
                update(Tenant).where(Tenant.tenant_code == tenant).values(status=1)
            )
            await session.commit()
        status = 503
        assert (await c.get("/api/v1/me")).status_code == 503
        assert c.cookies.get(auth.COOKIE)
        status = 403
        assert (await c.get("/api/v1/me")).status_code == 403
        from sqlalchemy.exc import SQLAlchemyError

        async def database_unavailable(*args):
            raise SQLAlchemyError("must-not-expose-database-details")

        with monkeypatch.context() as patch:
            status = 200
            patch.setattr(auth, "resolve_identity", database_unavailable)
            unavailable = await c.get("/api/v1/me")
            assert unavailable.status_code == 503
            assert "must-not-expose-database-details" not in unavailable.text
            assert c.cookies.get(auth.COOKIE)
        status = 401
        assert (await c.get("/api/v1/me")).status_code == 401
        status = 200
        c.cookies.set(auth.COOKIE, "sess_" + "t" * 43, domain="app.example.test", path="/")
        result = await c.post("/sso/logout-all", headers={"Origin": "https://app.example.test"})
        assert result.status_code == 200 and paths[-1] == "/sso/auth/logout-all"
        assert not c.cookies.get(auth.COOKIE)
        paths.clear()
        assert (
            await c.get("/api/v1/me", headers={"Authorization": "Bearer invalid"})
        ).status_code == 401
        assert not paths  # Invalid formal tokens must never be exchanged or sent upstream.
