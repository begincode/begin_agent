import json
import os
import socket
import time
from datetime import timedelta

from sqlalchemy import func, select, text
from test_platform_v1 import configured_agent, post

from app.api import auth
from app.core.config import Settings
from app.core.identity import LOCAL_TENANT, new_code
from app.db.schema import SCHEMA_REVISION
from app.models.platform import AgentTask
from app.services.diagnostics import database_snapshot, queue_snapshot, safe_queue_snapshot
from app.workers import readiness

pytest_plugins = ["test_platform_v1"]


def heartbeat(root, pid, **fields):
    root.mkdir(parents=True, exist_ok=True)
    value = dict(pid=pid, worker_id=f"{socket.gethostname()}:{pid}:sample", heartbeat=time.time())
    value.update(fields)
    (root / f"{pid}.json").write_text(json.dumps(value))


def test_worker_observation_distinguishes_stale_invalid_and_foreign(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=str(tmp_path), worker_lease_seconds=60)
    root = readiness.directory(settings)
    heartbeat(root, os.getpid())  # Older format without explicit hostname remains readable.
    heartbeat(root, 2000001, heartbeat=time.time() - 61)
    heartbeat(root, 2000002, hostname="another-host")
    heartbeat(root, 2000003, heartbeat=float("nan"))
    heartbeat(root, 2000004, heartbeat=time.time() + 10)
    (root / "2000005.json").write_text("[]")
    (root / "2000006.json").write_text("x" * 16385)
    heartbeat(root, 2000007, heartbeat=10**400)
    heartbeat(root, 2**40)
    result = readiness.inspect(settings)
    assert result["ready"] and result["active"] == 1
    assert result["stale"] == 1 and result["foreign"] == 1 and result["invalid"] == 6
    assert result["newest_age_seconds"] < 2 and "worker_id" not in result and "pid" not in result
    assert readiness.ready(settings)


def test_dead_and_unobservable_workers_are_not_reported_ready(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=str(tmp_path))
    root = readiness.directory(settings)
    heartbeat(root, 3000001)
    heartbeat(root, 3000002)

    def kill(pid, _):
        raise ProcessLookupError() if pid == 3000001 else PermissionError()

    monkeypatch.setattr(readiness.os, "kill", kill)
    result = readiness.inspect(settings)
    assert not result["ready"] and result["stale"] == 1 and result["unobservable"] == 1


def test_worker_report_is_atomic_and_scanning_is_bounded(tmp_path):
    settings = Settings(_env_file=None, data_dir=str(tmp_path))
    readiness.report(settings, f"{socket.gethostname()}:{os.getpid()}:instance")
    root = readiness.directory(settings)
    assert not list(root.glob("*.tmp"))
    value = json.loads((root / f"{os.getpid()}.json").read_text())
    assert value["hostname"] == socket.gethostname()
    for pid in range(4000000, 4000257):
        heartbeat(root, pid, hostname="remote")
    result = readiness.inspect(settings)
    assert result["truncated"] and sum(result[k] for k in ("active", "foreign")) == 256


async def test_database_probe_recovers_after_error_and_reports_migration_state(db):
    async with db() as session:
        assert (await database_snapshot(session))["status"] == "unavailable"
        await session.execute(text("CREATE TABLE alembic_version (version_num varchar(32))"))
        await session.execute(text("INSERT INTO alembic_version VALUES ('old')"))
        assert (await database_snapshot(session))["status"] == "migration_mismatch"
        await session.execute(
            text("UPDATE alembic_version SET version_num=:revision"), {"revision": SCHEMA_REVISION}
        )
        value = await database_snapshot(session)
        assert value["status"] == "ready" and value["latency_ms"] >= 0
        assert "old" not in str(value) and "url" not in str(value)


async def test_queue_time_boundaries_counts_samples_and_read_only(client, db):
    agent, version = await configured_agent(client)
    async with db() as session, session.begin():
        now = await session.scalar(select(func.now()))
        tasks = []

        def add(status="PENDING", **kwargs):
            task = AgentTask(
                tenant_code=LOCAL_TENANT,
                task_code=new_code("tsk"),
                agent_code=agent["agent_code"],
                agent_version_code=version["version_code"],
                created_by="fixture",
                title="受控诊断",
                input={"prompt": "never-return-this"},
                status=status,
                scheduled_at=now,
                **kwargs,
            )
            session.add(task)
            tasks.append(task)
            return task

        add()  # Exactly ready now.
        add().scheduled_at = now + timedelta(seconds=1)
        add(next_retry_at=now + timedelta(seconds=1))
        add().scheduled_at = now - timedelta(seconds=300)  # Inclusive warning boundary.
        add().scheduled_at = now - timedelta(seconds=299)
        add(next_retry_at=now - timedelta(seconds=30)).scheduled_at = now - timedelta(days=2)
        add("RUNNING", lease_expires_at=now)
        add("RUNNING", lease_expires_at=now + timedelta(seconds=1))
        add("RUNNING")
        add("FAILED", finished_at=now - timedelta(days=1))
        add("TIMEOUT", finished_at=now - timedelta(days=1, seconds=1))
        add("CANCELLED", finished_at=now)
        await session.flush()
        value = await queue_snapshot(session, LOCAL_TENANT)
        assert {
            k: value[k]
            for k in (
                "pending",
                "ready",
                "delayed",
                "running",
                "expired_leases",
                "missing_leases",
                "long_waiting",
                "failed_24h",
            )
        } == dict(
            pending=6,
            ready=4,
            delayed=2,
            running=3,
            expired_leases=1,
            missing_leases=1,
            long_waiting=1,
            failed_24h=1,
        )
        assert value["oldest_ready_seconds"] == 300 and len(value["samples"]) == 4
        assert {r["reason"] for r in value["samples"]} == {
            "LEASE_EXPIRED",
            "LEASE_MISSING",
            "LONG_WAIT",
            "RECENT_FAILURE",
        }
        assert "never-return-this" not in str(value) and "lease_token" not in str(value)
        assert [t.status for t in tasks] == ["PENDING"] * 6 + ["RUNNING"] * 3 + [
            "FAILED",
            "TIMEOUT",
            "CANCELLED",
        ]


async def test_status_requires_manager_and_queue_requires_task_permission(client, db):
    await post(client, "/roles", {"role_name": "仅运行状态", "permissions": ["tenant.manage"]})
    role = (await client.get("/api/v1/roles")).json()["roles"]
    code = next(r["role_code"] for r in role if r["role_name"] == "仅运行状态")
    for name, assigned in [("statusmanager", code), ("statusmember", "MEMBER")]:
        await post(
            client,
            "/users",
            {"username": name, "display_name": name, "password": "status-test-password-2026"},
        )
        await post(
            client, "/members", {"username": name, "display_name": name, "role_code": assigned}
        )
    auth.get_settings().auth_mode = "password"
    for name, expected in [("statusmember", 403), ("statusmanager", 200)]:
        response = await client.post(
            "/api/v1/auth/login", json={"username": name, "password": "status-test-password-2026"}
        )
        assert response.status_code == 200
        response = await client.get("/api/v1/integration-status")
        assert response.status_code == expected
        if expected == 200:
            assert response.json()["queue"] == {"status": "forbidden"}


async def test_queue_tenant_isolation_sample_limit_disabled_tenant(client, db):
    agent, version = await configured_agent(client)
    async with db() as session:
        for _ in range(13):
            session.add(
                AgentTask(
                    tenant_code=LOCAL_TENANT,
                    task_code=new_code("tsk"),
                    agent_code=agent["agent_code"],
                    agent_version_code=version["version_code"],
                    created_by="fixture",
                    title="异常任务",
                    input={},
                    status="RUNNING",
                )
            )
        await session.commit()
    value = (await client.get("/api/v1/integration-status")).json()
    assert value["queue"]["missing_leases"] == 13 and len(value["queue"]["samples"]) == 10
    other = await post(client, "/tenants", {"tenant_name": "诊断隔离租户"})
    response = await client.get(
        "/api/v1/integration-status", headers={"X-Tenant-Code": other["tenant_code"]}
    )
    assert response.json()["queue"]["running"] == 0 and response.json()["queue"]["samples"] == []
    await client.patch("/api/v1/tenants/" + other["tenant_code"], json={"status": 0})
    assert (
        await client.get(
            "/api/v1/integration-status", headers={"X-Tenant-Code": other["tenant_code"]}
        )
    ).status_code == 200


async def test_queue_failure_does_not_expose_error_or_fake_zero_counts(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from app.services import diagnostics

    async def failure(*args):
        raise SQLAlchemyError("postgresql://secret:credential@internal/tenant")

    monkeypatch.setattr(diagnostics, "queue_snapshot", failure)
    value = await safe_queue_snapshot(None, "tenant")
    assert value == {"status": "unavailable", "samples": []}
