"""Shared-database liveness and per-user acknowledgments; no external notifications."""

import asyncio
import hashlib
import json
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.models.workspace import DiagnosticAcknowledgment, WorkerPulse

THRESHOLD = 45


async def pulse(factory, instance, stopped=False):
    async with factory() as session, session.begin():
        await session.execute(
            insert(WorkerPulse)
            .values(
                instance_code=instance,
                heartbeat_at=func.clock_timestamp(),
                stopped=stopped,
            )
            .on_conflict_do_update(
                index_elements=["instance_code"],
                set_={
                    "heartbeat_at": func.clock_timestamp(),
                    "stopped": stopped,
                },
            )
        )


async def cluster_snapshot(session):
    try:
        async with asyncio.timeout(3), session.begin_nested():
            now = await session.scalar(select(func.now()))
            fresh = (
                WorkerPulse.heartbeat_at >= now - timedelta(seconds=THRESHOLD)
            ) & ~WorkerPulse.stopped
            active, stale, last = (
                await session.execute(
                    select(
                        func.count().filter(fresh),
                        func.count().filter(~fresh & ~WorkerPulse.stopped),
                        func.max(WorkerPulse.heartbeat_at),
                    )
                )
            ).one()
        return {
            "status": "ready",
            "active": active,
            "stale": stale,
            "last_seen": last,
            "threshold_seconds": THRESHOLD,
            "scope": "shared_database",
            "ready": active > 0,
        }
    except (SQLAlchemyError, TimeoutError):
        return {"status": "unavailable", "ready": False, "scope": "shared_database"}


def alert_candidates(cluster, queue):
    alerts = []
    if cluster.get("status") == "ready" and not cluster["ready"]:
        alerts.append(("NO_WORKER", "未观测到有效数据库 Worker 心跳", cluster.get("last_seen")))
    if queue.get("status") == "ready":
        for key, title in [
            ("expired_leases", "任务租约已过期"),
            ("missing_leases", "运行任务缺少租约"),
            ("long_waiting", "任务可领取后等待超过五分钟"),
            ("failed_24h", "最近一天有失败或超时任务"),
        ]:
            if queue.get(key, 0):
                # New sample identity/time reopens acknowledgment.
                alerts.append((key.upper(), title, [queue[key], queue.get("samples", [])]))
    return [
        {
            "code": code,
            "message": title,
            "fingerprint": hashlib.sha256(
                json.dumps([code, observation], sort_keys=True, default=str).encode()
            ).hexdigest(),
        }
        for code, title, observation in alerts
    ]


async def alerts_for(session, principal, cluster, queue):
    alerts = alert_candidates(cluster, queue)
    async with asyncio.timeout(3), session.begin_nested():
        acknowledged = set(
            (
                await session.scalars(
                    select(DiagnosticAcknowledgment.fingerprint).where(
                        DiagnosticAcknowledgment.tenant_code == principal.tenant_code,
                        DiagnosticAcknowledgment.user_code == principal.user_code,
                        DiagnosticAcknowledgment.fingerprint.in_(
                            [a["fingerprint"] for a in alerts]
                        ),
                    )
                )
            ).all()
        )
    return [{**a, "acknowledged": a["fingerprint"] in acknowledged} for a in alerts]
