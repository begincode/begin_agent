import asyncio
import json

from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_platform_v1 import conditional, post

from app.api import auth
from app.core.identity import LOCAL_TENANT
from app.models.platform import Agent
from app.models.workspace import AgentCreationReceipt, AuditEvent

pytest_plugins = ["test_platform_v1"]
KEY = "creation-test-request-0001"
DATA = {"agent_name": "幂等创建助手", "agent_type": "NORMAL", "status": 1}


async def create(client, data=None, key=KEY, **kwargs):
    headers = {"Idempotency-Key": key, **kwargs.pop("headers", {})}
    return await client.post("/api/v1/agents", json=data or DATA, headers=headers, **kwargs)


async def test_creation_replay_and_lookup_return_original_result_and_mark_audit(client, db):
    first, replay = await create(client), await create(client)
    assert first.status_code == 201 and replay.status_code == 200
    assert first.json() == replay.json()
    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.headers["Idempotency-Replayed"] == "true"
    found = await client.get("/api/v1/agents/creation-requests/" + KEY)
    assert found.status_code == 200 and found.json() == first.json()
    assert found.headers["cache-control"] == "no-store"
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Agent)) == 1
        receipts = (await session.scalars(select(AgentCreationReceipt))).all()
        assert len(receipts) == 1
        assert receipts[0].request_hash != KEY and KEY not in str(receipts[0].result)
        audits = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "POST /api/v1/agents")
            )
        ).all()
        assert len(audits) == 2
        assert sum(bool(a.details.get("idempotent_replay")) for a in audits) == 1


async def test_canonical_content_replays_but_same_key_different_content_conflicts(client):
    first = await create(client, {"agent_name": "  规范化名称  "})
    replay = await create(client, {"agent_name": "规范化名称", "agent_type": "NORMAL", "status": 1})
    assert replay.status_code == 200 and replay.json() == first.json()
    changed = await create(client, {"agent_name": "另一个意图"})
    assert changed.status_code == 409
    assert len((await client.get("/api/v1/agents")).json()) == 1


async def test_concurrent_requests_have_one_committed_agent_and_receipt(client, db):
    results = await asyncio.gather(*(create(client) for _ in range(10)))
    assert sorted(r.status_code for r in results) == [200] * 9 + [201]
    assert len({r.json()["agent_code"] for r in results}) == 1
    assert all(r.json() == results[0].json() for r in results)
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(AgentCreationReceipt)) == 1
        assert await session.scalar(select(func.count()).select_from(Agent)) == 1


async def test_replay_does_not_refresh_initial_etag_or_rewrite_changed_agent(client):
    first = (await create(client)).json()
    code = first["agent_code"]
    changed = await conditional(
        client,
        "PATCH",
        "/api/v1/agents/" + code,
        json={**DATA, "agent_name": "后来编辑者保存的名称"},
    )
    assert changed.status_code == 200
    replay = await create(client)
    assert replay.status_code == 200 and replay.json() == first
    found = (await client.get("/api/v1/agents/creation-requests/" + KEY)).json()
    assert found["configuration_etag"] == first["configuration_etag"]
    current = (await client.get(f"/api/v1/agents/{code}/configuration")).json()
    assert current["agent"]["agent_name"] == "后来编辑者保存的名称"
    assert current["etag"] != first["configuration_etag"]


async def test_receipts_are_scoped_by_tenant_and_actor_even_for_platform_admin(client):
    first = (await create(client)).json()
    other = await post(client, "/tenants", {"tenant_name": "创建请求隔离"})
    headers = {"X-Tenant-Code": other["tenant_code"]}
    assert (
        await client.get("/api/v1/agents/creation-requests/" + KEY, headers=headers)
    ).status_code == 404
    in_other = await create(client, headers=headers)
    assert in_other.status_code == 201 and in_other.json()["agent_code"] != first["agent_code"]
    role = await post(
        client, "/roles", {"role_name": "仅创建管理", "permissions": ["agents.manage"]}
    )
    await post(
        client,
        "/users",
        {
            "username": "receiptowner",
            "display_name": "另一个创建者",
            "password": "creation-test-password-2026",
        },
    )
    await post(
        client,
        "/members",
        {
            "username": "receiptowner",
            "display_name": "另一个创建者",
            "role_code": role["role_code"],
        },
    )
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "receiptowner", "password": "creation-test-password-2026"},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/agents/creation-requests/" + KEY)).status_code == 404
    owned = await create(client)
    assert owned.status_code == 201 and owned.json()["agent_code"] != first["agent_code"]
    assert (await client.get("/api/v1/agents/creation-requests/" + KEY)).json() == owned.json()


async def test_disabled_tenant_and_readonly_account_cannot_replay_creation(client):
    first = (await create(client)).json()
    assert (
        await client.patch(
            "/api/v1/tenants/" + LOCAL_TENANT, json={"tenant_name": "停用创建租户", "status": 0}
        )
    ).status_code == 200
    assert (await create(client)).status_code == 403
    assert (await client.get("/api/v1/agents/creation-requests/" + KEY)).json() == first
    assert (
        await client.patch(
            "/api/v1/tenants/" + LOCAL_TENANT, json={"tenant_name": "恢复创建租户", "status": 1}
        )
    ).status_code == 200
    role = await post(client, "/roles", {"role_name": "创建只读", "permissions": ["agents.read"]})
    await post(
        client,
        "/users",
        {
            "username": "receiptreader",
            "display_name": "只读",
            "password": "creation-test-password-2026",
        },
    )
    await post(
        client,
        "/members",
        {"username": "receiptreader", "display_name": "只读", "role_code": role["role_code"]},
    )
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "receiptreader", "password": "creation-test-password-2026"},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/agents/creation-requests/" + KEY)).status_code == 403
    assert (await create(client)).status_code == 403


async def test_receipt_failure_rolls_back_agent_and_request_can_retry(client, db):
    def reject_receipt(session, *_):
        if any(isinstance(row, AgentCreationReceipt) for row in session.new):
            raise IntegrityError("injected receipt failure", {}, Exception("fixture"))

    event.listen(Session, "before_flush", reject_receipt)
    try:
        assert (await create(client)).status_code == 409
    finally:
        event.remove(Session, "before_flush", reject_receipt)
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Agent)) == 0
        assert await session.scalar(select(func.count()).select_from(AgentCreationReceipt)) == 0
    assert (await create(client)).status_code == 201


async def test_failure_after_commit_can_replay_without_duplicate_agent(client, db):
    failed = False

    def fail_audit_once(session, *_):
        nonlocal failed
        if not failed and any(isinstance(row, AuditEvent) for row in session.new):
            failed = True
            raise IntegrityError("injected post-commit failure", {}, Exception("fixture"))

    event.listen(Session, "before_flush", fail_audit_once)
    try:
        assert (await create(client)).status_code == 503
        result = await create(client)
        assert result.status_code == 200 and result.headers["Idempotency-Replayed"] == "true"
    finally:
        event.remove(Session, "before_flush", fail_audit_once)
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Agent)) == 1
        assert await session.scalar(select(func.count()).select_from(AgentCreationReceipt)) == 1


async def test_invalid_keys_and_missing_lookup_never_create_and_legacy_remains_explicit(client, db):
    for key in ("short", "x" * 129, "bad key with whitespace", "!" * 20):
        assert (await create(client, key=key)).status_code == 422
    assert (await client.get("/api/v1/agents/creation-requests/" + KEY)).status_code == 404
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Agent)) == 0
    first = await client.post("/api/v1/agents", json=DATA)
    second = await client.post("/api/v1/agents", json=DATA)
    assert first.status_code == second.status_code == 201
    assert first.json()["agent_code"] != second.json()["agent_code"]
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(AgentCreationReceipt)) == 0


async def test_payload_does_not_persist_unknown_fields_or_credentials(client):
    value = await create(client, {**DATA, "api_key": "must-not-persist"})
    assert value.status_code == 422
    assert (await client.get("/api/v1/agents/creation-requests/" + KEY)).status_code == 404
    result = await create(client)
    assert "must-not-persist" not in json.dumps(result.json())
