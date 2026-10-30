"""Read-only execution projections; never return raw messages or tool payloads."""

from datetime import UTC, datetime
from math import isfinite

from sqlalchemy import select

from app.models.platform import Agent, AgentRun, AgentTask, RunStep


def token_count(usage):
    if not isinstance(usage, dict):
        return None

    def number(value):
        return (
            int(value)
            if type(value) in (int, float)
            and 0 <= value <= 9007199254740991
            and (type(value) is int or isfinite(value))
            else None
        )

    total = number(usage.get("total_tokens"))
    if total is not None:
        return total
    values = [number(usage.get(k)) for k in ("prompt_tokens", "completion_tokens")]
    return sum(v for v in values if v is not None) if any(v is not None for v in values) else None


def sum_known(values):
    known = [v for v in values if v is not None]
    return sum(known) if known else None


async def run_summaries(session, repo, runs, tasks_visible=True, agents_visible=True):
    """Batch a bounded page of summaries without loading model or tool payloads."""
    if not runs:
        return []
    names = {}
    titles = {}
    if agents_visible:
        names = dict(
            (
                await session.execute(
                    select(Agent.agent_code, Agent.agent_name).where(
                        Agent.tenant_code == repo.tenant_code,
                        Agent.agent_code.in_({r.agent_code for r in runs}),
                    )
                )
            ).all()
        )
    if tasks_visible:
        titles = dict(
            (
                await session.execute(
                    select(AgentTask.task_code, AgentTask.title).where(
                        AgentTask.tenant_code == repo.tenant_code,
                        AgentTask.task_code.in_({r.task_code for r in runs}),
                    )
                )
            ).all()
        )
    steps = (
        await session.execute(
            select(
                RunStep.run_code,
                RunStep.step_type,
                RunStep.status,
                RunStep.output["usage"].label("usage"),
            ).where(
                RunStep.tenant_code == repo.tenant_code,
                RunStep.run_code.in_([r.run_code for r in runs]),
            )
        )
    ).all()
    by_run = {r.run_code: [] for r in runs}
    for step in steps:
        by_run[step.run_code].append(step)
    now = datetime.now(UTC)
    result = []
    for run in runs:
        steps = by_run[run.run_code]
        usage = [token_count(s.usage) for s in steps if s.step_type == "MODEL_CALL"]
        result.append(
            {
                "run_code": run.run_code,
                "task_code": run.task_code,
                "task_title": titles.get(run.task_code),
                "agent_code": run.agent_code,
                "agent_name": names.get(run.agent_code),
                "agent_version_code": run.agent_version_code,
                "turn_no": run.turn_no,
                "parent_run_code": run.parent_run_code,
                "root_run_code": run.root_run_code,
                "run_depth": run.run_depth,
                "status": run.status,
                "finish_reason": run.finish_reason,
                "created_at": run.created_at,
                "finished_at": run.finished_at,
                "elapsed_ms": max(
                    0, int(((run.finished_at or now) - run.created_at).total_seconds() * 1000)
                ),
                "steps_count": len(steps),
                "failed_steps": sum(s.status == "FAILED" for s in steps),
                "model_calls": len(usage),
                "unreported_calls": sum(v is None for v in usage),
                "tokens": sum_known(usage),
            }
        )
    return result


async def summarize_execution(session, repo, task):
    rows = (
        await session.execute(
            repo.query(AgentRun)
            .outerjoin(
                Agent,
                (Agent.agent_code == AgentRun.agent_code)
                & (Agent.tenant_code == AgentRun.tenant_code),
            )
            .add_columns(Agent.agent_name)
            .where(AgentRun.task_code == task.task_code)
            .order_by(AgentRun.created_at, AgentRun.run_code)
        )
    ).all()
    codes = [r.run_code for r, _ in rows]
    by_run = {code: [] for code in codes}
    if codes:
        steps = (
            await session.execute(
                select(
                    RunStep.run_code,
                    RunStep.step_code,
                    RunStep.step_no,
                    RunStep.step_type,
                    RunStep.name,
                    RunStep.status,
                    RunStep.error_code,
                    RunStep.output["usage"].label("usage"),
                )
                .where(RunStep.tenant_code == repo.tenant_code, RunStep.run_code.in_(codes))
                .order_by(RunStep.step_no)
            )
        ).all()
        for step in steps:
            by_run[step.run_code].append(
                {
                    "step_code": step.step_code,
                    "step_no": step.step_no,
                    "step_type": step.step_type,
                    "name": step.name,
                    "status": step.status,
                    "error_code": step.error_code,
                    "tokens": token_count(step.usage) if step.step_type == "MODEL_CALL" else None,
                }
            )
    now = datetime.now(UTC)
    runs = []
    for run, name in rows:
        steps = by_run[run.run_code]
        runs.append(
            {
                "run_code": run.run_code,
                "agent_code": run.agent_code,
                "agent_name": name or "Agent",
                "version_code": run.agent_version_code,
                "parent_run_code": run.parent_run_code,
                "root_run_code": run.root_run_code,
                "run_depth": run.run_depth,
                "turn_no": run.turn_no,
                "status": run.status,
                "finish_reason": run.finish_reason,
                "created_at": run.created_at,
                "elapsed_ms": max(
                    0, int(((run.finished_at or now) - run.created_at).total_seconds() * 1000)
                ),
                "steps_count": len(steps),
                "failed_steps": sum(s["status"] == "FAILED" for s in steps),
                "tokens": sum_known(s["tokens"] for s in steps),
                "steps": steps,
            }
        )
    return {
        "task_code": task.task_code,
        "status": task.status,
        "run_count": len(runs),
        "tokens": sum_known(r["tokens"] for r in runs),
        "steps_count": sum(r["steps_count"] for r in runs),
        "runs": runs,
    }
