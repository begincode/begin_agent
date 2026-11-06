from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from test_platform_v1 import post

from app.models.workspace import DiagnosticAcknowledgment, WorkerPulse
from app.services.operations import alert_candidates, cluster_snapshot, pulse

pytest_plugins = ["test_platform_v1"]


@pytest.mark.asyncio
async def test_database_pulses_share_liveness_without_host_identity(db):
    await pulse(db, "remote-instance-one")
    await pulse(db, "remote-instance-two")
    async with db() as session:
        value = await cluster_snapshot(session)
        assert value["active"] == 2 and value["ready"] and "instance_code" not in str(value)
    await pulse(db, "remote-instance-one", stopped=True)
    async with db() as session, session.begin():
        await session.execute(
            update(WorkerPulse)
            .where(WorkerPulse.instance_code == "remote-instance-two")
            .values(heartbeat_at=func.now() - timedelta(seconds=46))
        )
    async with db() as session:
        value = await cluster_snapshot(session)
        assert value["active"] == 0 and value["stale"] == 1 and not value["ready"]


@pytest.mark.asyncio
async def test_alert_confirmation_is_scoped_idempotent_and_not_a_repair(client, db):
    current = (await client.get("/api/v1/integration-status")).json()
    alert = next(a for a in current["alerts"] if a["code"] == "NO_WORKER")
    url = f"/api/v1/integration-status/alerts/{alert['fingerprint']}/ack"
    assert (await client.post(url, json={})).status_code == 200
    assert (await client.post(url, json={})).status_code == 200
    current = (await client.get("/api/v1/integration-status")).json()
    assert next(a for a in current["alerts"] if a["code"] == "NO_WORKER")["acknowledged"]
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(DiagnosticAcknowledgment)) == 1
    tenant = await post(client, "/tenants", {"tenant_name": "其他告警租户"})
    other = (
        await client.get(
            "/api/v1/integration-status", headers={"X-Tenant-Code": tenant["tenant_code"]}
        )
    ).json()
    assert not next(a for a in other["alerts"] if a["code"] == "NO_WORKER")["acknowledged"]
    await pulse(db, "recovered-instance")
    assert (await client.post(url, json={})).status_code == 409
    assert not (await client.get("/api/v1/integration-status")).json()["alerts"]


def test_changed_queue_sample_reopens_alert():
    cluster = {"status": "ready", "ready": True}
    first = alert_candidates(
        cluster, {"status": "ready", "failed_24h": 1, "samples": [{"task_code": "one"}]}
    )
    same = alert_candidates(
        cluster, {"status": "ready", "failed_24h": 1, "samples": [{"task_code": "one"}]}
    )
    changed = alert_candidates(
        cluster, {"status": "ready", "failed_24h": 1, "samples": [{"task_code": "two"}]}
    )
    assert first == same and first[0]["fingerprint"] != changed[0]["fingerprint"]
