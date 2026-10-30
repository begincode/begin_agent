from datetime import UTC, datetime

from fastapi import HTTPException

from app.core.identity import new_code
from app.models.platform import Agent, AgentTask, AgentVersion
from app.models.workspace import TaskTurn
from app.repositories.platform import TenantRepository
from app.services.permissions import allowed


def authorize_task(principal, task):
    if task.created_by != principal.user_code and not allowed(principal, "tasks.manage"):
        raise HTTPException(403, "只能操作自己发起的任务")


async def enqueue(session, principal, agent_code, version_code, title, prompt, extra=None):
    repo = TenantRepository(session, principal.tenant_code)
    agent = await repo.get(Agent, "agent_code", agent_code)
    version = await repo.get(AgentVersion, "version_code", version_code)
    if version.agent_code != agent_code or agent.status != 1:
        raise HTTPException(409, "Agent 或版本不可执行")
    row = AgentTask(
        tenant_code=principal.tenant_code,
        task_code=new_code("tsk"),
        agent_code=agent_code,
        agent_version_code=version_code,
        created_by=principal.user_code,
        title=title,
        input={"prompt": prompt, **(extra or {})},
    )
    session.add(row)
    await session.flush()
    session.add(
        TaskTurn(
            tenant_code=principal.tenant_code,
            turn_code=new_code("trn"),
            task_code=row.task_code,
            turn_no=1,
            prompt=prompt,
        )
    )
    await session.commit()
    return row


async def append_turn(session, principal, code, data, retry=False, rerun_from=None):
    repo = TenantRepository(session, principal.tenant_code)
    task = await repo.get(AgentTask, "task_code", code, lock=True)
    authorize_task(principal, task)
    if data.idempotency_key:
        old = await session.scalar(
            repo.query(TaskTurn).where(
                TaskTurn.task_code == code, TaskTurn.idempotency_key == data.idempotency_key
            )
        )
        if old:
            if old.prompt != data.prompt:
                raise HTTPException(409, "幂等键已用于其他消息")
            return task, old
    if task.status in {"PENDING", "RUNNING"}:
        raise HTTPException(409, "当前轮次尚未结束，请等待或取消后继续")
    if rerun_from and task.status != "SUCCEEDED":
        raise HTTPException(409, "只有成功任务可重新执行，失败或取消任务请使用重试")
    if task.input.get("test_case_code") and not retry:
        raise HTTPException(409, "测试任务请重新运行用例")
    agent = await repo.get(Agent, "agent_code", task.agent_code)
    if agent.status != 1:
        raise HTTPException(409, "Agent 已停用")
    previous = await session.scalar(
        repo.query(TaskTurn)
        .where(TaskTurn.task_code == code)
        .order_by(TaskTurn.turn_no.desc())
        .limit(1)
    )
    if rerun_from and (not previous or previous.turn_code != rerun_from):
        raise HTTPException(409, "任务轮次已更新，请刷新后重新执行")
    turn_no = previous.turn_no + 1 if previous else 2
    if turn_no > 100:
        raise HTTPException(409, "单个任务最多 100 轮，请新建任务")
    row = TaskTurn(
        tenant_code=principal.tenant_code,
        turn_code=new_code("trn"),
        task_code=code,
        turn_no=turn_no,
        prompt=data.prompt,
        idempotency_key=data.idempotency_key,
    )
    session.add(row)
    task.input = {
        **task.input,
        "initial_prompt": task.input.get("initial_prompt", task.input["prompt"]),
        "prompt": data.prompt,
    }
    task.status, task.output, task.error_code = "PENDING", None, None
    task.retry_count, task.cancel_requested = 0, False
    task.finished_at, task.next_retry_at = None, None
    task.locked_by, task.locked_at, task.lease_token, task.lease_expires_at = None, None, None, None
    task.scheduled_at = datetime.now(UTC)
    await session.commit()
    return task, row
