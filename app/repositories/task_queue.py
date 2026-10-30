"""Lease tokens fence every write. No model/tool call holds a DB transaction."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update

from app.core.identity import new_code
from app.models.platform import Agent, AgentRun, AgentTask, Tenant
from app.models.workspace import TaskTurn


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class Lease:
    task_code: str
    tenant_code: str
    token: str
    version_code: str
    prompt: str
    turn_no: int = 1


class TaskQueue:
    def __init__(self, factory, worker_id, lease_seconds=60):
        self.factory = factory
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    async def claim(self):
        async with self.factory() as session, session.begin():
            task = await session.scalar(
                select(AgentTask)
                .where(
                    or_(
                        and_(
                            AgentTask.status == "PENDING",
                            AgentTask.scheduled_at <= func.now(),
                            or_(
                                AgentTask.next_retry_at.is_(None),
                                AgentTask.next_retry_at <= func.now(),
                            ),
                        ),
                        and_(
                            AgentTask.status == "RUNNING", AgentTask.lease_expires_at <= func.now()
                        ),
                    )
                )
                .order_by(AgentTask.priority.desc(), AgentTask.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return None
            now = await session.scalar(select(func.clock_timestamp()))
            enabled = await session.scalar(
                select(Tenant.status).where(Tenant.tenant_code == task.tenant_code)
            )
            agent_enabled = await session.scalar(
                select(Agent.status).where(
                    Agent.tenant_code == task.tenant_code, Agent.agent_code == task.agent_code
                )
            )
            if enabled != 1 or agent_enabled != 1:
                task.status, task.error_code, task.finished_at = "FAILED", "CONFIG_DISABLED", now
                task.lease_token, task.lease_expires_at = None, None
                await session.execute(
                    update(TaskTurn)
                    .where(
                        TaskTurn.tenant_code == task.tenant_code,
                        TaskTurn.task_code == task.task_code,
                        TaskTurn.status.in_(["RUNNING", "PENDING"]),
                    )
                    .values(status="FAILED", finished_at=now)
                )
                await session.execute(
                    update(AgentRun)
                    .where(
                        AgentRun.task_code == task.task_code,
                        AgentRun.tenant_code == task.tenant_code,
                        AgentRun.status == "RUNNING",
                    )
                    .values(status="FAILED", finish_reason="CANCELLED", finished_at=now)
                )
                return None
            if task.status == "RUNNING":
                await session.execute(
                    update(AgentRun)
                    .where(
                        AgentRun.task_code == task.task_code,
                        AgentRun.tenant_code == task.tenant_code,
                        AgentRun.status == "RUNNING",
                    )
                    .values(status="FAILED", finish_reason="TIMEOUT", finished_at=now)
                )
                task.retry_count += 1
                if task.retry_count > task.max_retries:
                    task.status, task.error_code, task.finished_at = "FAILED", "LEASE_EXPIRED", now
                    task.lease_token, task.lease_expires_at = None, None
                    await session.execute(
                        update(TaskTurn)
                        .where(
                            TaskTurn.tenant_code == task.tenant_code,
                            TaskTurn.task_code == task.task_code,
                            TaskTurn.status.in_(["RUNNING", "PENDING"]),
                        )
                        .values(status="FAILED", finished_at=now)
                    )
                    return None
            task.status = "RUNNING"
            turn = await session.scalar(
                select(TaskTurn)
                .where(
                    TaskTurn.tenant_code == task.tenant_code, TaskTurn.task_code == task.task_code
                )
                .order_by(TaskTurn.turn_no.desc())
                .limit(1)
            )
            if turn:
                turn.status = "RUNNING"
            task.locked_by, task.locked_at = self.worker_id, now
            task.lease_token = new_code("lse")
            task.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            return Lease(
                task.task_code,
                task.tenant_code,
                task.lease_token,
                task.agent_version_code,
                task.input["prompt"],
                turn.turn_no if turn else 1,
            )

    async def owned(self, session, lease):
        task = await session.scalar(
            select(AgentTask)
            .where(
                AgentTask.task_code == lease.task_code,
                AgentTask.tenant_code == lease.tenant_code,
                AgentTask.lease_token == lease.token,
                AgentTask.status == "RUNNING",
                AgentTask.cancel_requested.is_(False),
                AgentTask.lease_expires_at > func.clock_timestamp(),
            )
            .with_for_update()
        )
        if task is None:
            raise LeaseLost()
        return task

    async def heartbeat(self, lease):
        async with self.factory() as session, session.begin():
            task = await self.owned(session, lease)
            now = await session.scalar(select(func.clock_timestamp()))
            task.lease_expires_at = now + timedelta(seconds=self.lease_seconds)

    async def finish(self, lease, status, output=None, reason=None, retryable=False):
        async with self.factory() as session, session.begin():
            task = await self.owned(session, lease)
            task.error_code = reason
            if status == "SUCCEEDED" and task.input.get("expected_contains"):
                output = {
                    **(output or {}),
                    "test_result": {
                        "passed": task.input["expected_contains"] in (output or {}).get("text", ""),
                        "expected_contains": task.input["expected_contains"],
                        "case_code": task.input.get("test_case_code"),
                    },
                }
            if retryable and task.retry_count < task.max_retries:
                task.retry_count += 1
                task.status = "PENDING"
                task.next_retry_at = datetime.now(UTC) + timedelta(
                    seconds=min(60, 2**task.retry_count)
                )
            else:
                task.status, task.output, task.finished_at = status, output, datetime.now(UTC)
            task.lease_token, task.lease_expires_at, task.locked_by = None, None, None
            turn = await session.scalar(
                select(TaskTurn).where(
                    TaskTurn.tenant_code == lease.tenant_code,
                    TaskTurn.task_code == lease.task_code,
                    TaskTurn.turn_no == lease.turn_no,
                )
            )
            if turn:
                turn.status, turn.output = task.status, task.output
                turn.finished_at = task.finished_at
                turn.run_code = (output or {}).get("run_code") or await session.scalar(
                    select(AgentRun.run_code)
                    .where(
                        AgentRun.task_code == lease.task_code,
                        AgentRun.tenant_code == lease.tenant_code,
                        AgentRun.lease_token == lease.token,
                        AgentRun.parent_run_code.is_(None),
                    )
                    .limit(1)
                )
            if status != "SUCCEEDED":
                await session.execute(
                    update(AgentRun)
                    .where(
                        AgentRun.tenant_code == lease.tenant_code,
                        AgentRun.task_code == lease.task_code,
                        AgentRun.lease_token == lease.token,
                        AgentRun.status == "RUNNING",
                    )
                    .values(status=status, finish_reason=reason, finished_at=datetime.now(UTC))
                )

    async def write(self, lease, *rows):
        async with self.factory() as session, session.begin():
            await self.owned(session, lease)
            session.add_all(rows)

    async def update_live(self, lease, run_code, value):
        async with self.factory() as session, session.begin():
            await self.owned(session, lease)
            result = await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.tenant_code == lease.tenant_code,
                    AgentRun.run_code == run_code,
                    AgentRun.lease_token == lease.token,
                    AgentRun.status == "RUNNING",
                )
                .values(output={"live": value})
            )
            if result.rowcount != 1:
                raise LeaseLost()

    async def finish_run(self, lease, run_code, output=None, reason="COMPLETED"):
        async with self.factory() as session, session.begin():
            await self.owned(session, lease)
            row = await session.scalar(
                select(AgentRun).where(
                    AgentRun.tenant_code == lease.tenant_code,
                    AgentRun.run_code == run_code,
                    AgentRun.lease_token == lease.token,
                    AgentRun.status == "RUNNING",
                )
            )
            if row:
                row.output = {**(row.output or {}), **(output or {})} or None
                row.status = "SUCCEEDED" if reason == "COMPLETED" else "FAILED"
                row.finish_reason = reason
                row.finished_at = datetime.now(UTC)
