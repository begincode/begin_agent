"""Read-only operational samples. Never return DSNs, process identities or task contents."""

import asyncio
from datetime import timedelta
from time import perf_counter

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.schema import SCHEMA_REVISION
from app.models.platform import AgentTask as Task

EXPECTED_REVISION = SCHEMA_REVISION
WAIT_WARNING_SECONDS = 300


async def database_snapshot(session):
    started = perf_counter()
    try:
        async with asyncio.timeout(3), session.begin_nested():
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        return {
            "status": "ready" if revision == EXPECTED_REVISION else "migration_mismatch",
            "migration_ready": revision == EXPECTED_REVISION,
            "expected_revision": EXPECTED_REVISION,
            "latency_ms": round((perf_counter() - started) * 1000, 1),
        }
    except (SQLAlchemyError, TimeoutError):
        return {"status": "unavailable", "migration_ready": False}


async def queue_snapshot(session, tenant_code):
    async with asyncio.timeout(3), session.begin_nested():
        now = await session.scalar(select(func.now()))
        pending, running = Task.status == "PENDING", Task.status == "RUNNING"
        eligible_at = func.greatest(
            Task.scheduled_at, func.coalesce(Task.next_retry_at, Task.scheduled_at)
        )
        ready = and_(pending, eligible_at <= now)
        waiting = and_(ready, eligible_at <= now - timedelta(seconds=WAIT_WARNING_SECONDS))
        expired = and_(running, Task.lease_expires_at <= now)
        missing = and_(running, Task.lease_expires_at.is_(None))
        failed = and_(
            Task.status.in_(["FAILED", "TIMEOUT"]), Task.finished_at >= now - timedelta(days=1)
        )
        conditions = dict(
            pending=pending,
            ready=ready,
            delayed=and_(pending, eligible_at > now),
            running=running,
            expired_leases=expired,
            missing_leases=missing,
            long_waiting=waiting,
            failed_24h=failed,
        )
        counts = (
            (
                await session.execute(
                    select(
                        *(
                            func.count().filter(condition).label(name)
                            for name, condition in conditions.items()
                        ),
                        func.min(eligible_at).filter(ready).label("oldest_ready_at"),
                    ).where(Task.tenant_code == tenant_code)
                )
            )
            .mappings()
            .one()
        )
        reason = case(
            (expired, "LEASE_EXPIRED"),
            (missing, "LEASE_MISSING"),
            (waiting, "LONG_WAIT"),
            else_="RECENT_FAILURE",
        )
        observed_at = case(
            (running, Task.lease_expires_at), (pending, eligible_at), else_=Task.finished_at
        )
        samples = (
            (
                await session.execute(
                    select(
                        Task.task_code,
                        Task.title,
                        Task.status,
                        reason.label("reason"),
                        observed_at.label("observed_at"),
                    )
                    .where(Task.tenant_code == tenant_code, or_(expired, missing, waiting, failed))
                    .order_by(
                        case((running, 0), (pending, 1), else_=2),
                        observed_at.asc().nullsfirst(),
                        Task.task_code,
                    )
                    .limit(10)
                )
            )
            .mappings()
            .all()
        )
        oldest = counts["oldest_ready_at"]
        return {
            "status": "ready",
            "sampled_at": now,
            **{name: counts[name] for name in conditions},
            "oldest_ready_seconds": max(0, round((now - oldest).total_seconds())) if oldest else 0,
            "wait_warning_seconds": WAIT_WARNING_SECONDS,
            "samples": [dict(row) for row in samples],
            "sample_limit": 10,
        }


async def safe_queue_snapshot(session, tenant_code):
    try:
        return await queue_snapshot(session, tenant_code)
    except (SQLAlchemyError, TimeoutError):
        return {"status": "unavailable", "samples": []}
