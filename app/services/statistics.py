"""Tenant-scoped operational metrics from recorded tasks, runs and model calls."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Date, func, select

from app.models.platform import AgentRun, AgentTask, AgentVersion, RunStep
from app.services.execution import sum_known, token_count

ZONE = ZoneInfo("Asia/Shanghai")


def date_window(days, now=None):
    today = (now or datetime.now(UTC)).astimezone(ZONE).date()
    start = today - timedelta(days=days - 1)
    return start, today, datetime.combine(start, time.min, ZONE).astimezone(UTC)


async def activity_statistics(session, repo, days, tasks_visible, runs_visible):
    start, today, boundary = date_window(days)
    end = datetime.combine(today + timedelta(days=1), time.min, ZONE).astimezone(UTC)
    result = {
        "days": days,
        "timezone": "Asia/Shanghai",
        "from_date": str(start),
        "to_date": str(today),
        "task_count": None,
        "success_rate": None,
        "daily": [],
        "run_count": None,
        "average_run_ms": None,
        "model_calls": None,
        "tokens": None,
        "unreported_calls": None,
        "model_usage": [],
    }
    if tasks_visible:
        day = func.timezone("Asia/Shanghai", AgentTask.created_at).cast(Date)
        records = (
            await session.execute(
                select(day.label("day"), AgentTask.status, func.count().label("count"))
                .where(
                    AgentTask.tenant_code == repo.tenant_code,
                    AgentTask.created_at >= boundary,
                    AgentTask.created_at < end,
                )
                .group_by(day, AgentTask.status)
            )
        ).all()
        daily = {
            str(start + timedelta(days=i)): {
                "date": str(start + timedelta(days=i)),
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "cancelled": 0,
            }
            for i in range(days)
        }
        for r in records:
            if str(r.day) not in daily:
                continue
            entry = daily[str(r.day)]
            entry["total"] += r.count
            field = {
                "SUCCEEDED": "succeeded",
                "FAILED": "failed",
                "TIMEOUT": "failed",
                "CANCELLED": "cancelled",
            }.get(r.status)
            if field:
                entry[field] += r.count
        result["daily"] = list(daily.values())
        result["task_count"] = sum(d["total"] for d in daily.values())
        success = sum(d["succeeded"] for d in daily.values())
        finished = success + sum(d["failed"] for d in daily.values())
        result["success_rate"] = round(success / finished * 100, 1) if finished else None
    if runs_visible:
        records = (
            await session.execute(
                repo.query(AgentRun)
                .outerjoin(
                    AgentVersion,
                    (AgentVersion.version_code == AgentRun.agent_version_code)
                    & (AgentVersion.tenant_code == repo.tenant_code),
                )
                .with_only_columns(
                    AgentRun.run_code,
                    AgentRun.created_at,
                    AgentRun.finished_at,
                    AgentVersion.model_snapshot,
                )
                .where(AgentRun.created_at >= boundary, AgentRun.created_at < end)
            )
        ).all()
        result["run_count"] = len(records)
        durations = [
            max(0, (r.finished_at - r.created_at).total_seconds() * 1000)
            for r in records
            if r.finished_at
        ]
        result["average_run_ms"] = round(sum(durations) / len(durations)) if durations else None
        by_run = {r.run_code: (r.model_snapshot or {}) for r in records}
        usage = (
            (
                await session.execute(
                    select(RunStep.run_code, RunStep.output["usage"].label("usage"))
                    .join(
                        AgentRun,
                        (AgentRun.run_code == RunStep.run_code)
                        & (AgentRun.tenant_code == RunStep.tenant_code),
                    )
                    .where(
                        RunStep.tenant_code == repo.tenant_code,
                        AgentRun.created_at >= boundary,
                        AgentRun.created_at < end,
                        RunStep.step_type == "MODEL_CALL",
                    )
                )
            ).all()
            if records
            else []
        )
        groups = {}
        for record in usage:
            model = by_run.get(record.run_code, {})
            key = (model.get("provider_code", "unknown"), model.get("model_code", "unknown"))
            group = groups.setdefault(
                key,
                {
                    "provider_code": key[0],
                    "model_code": key[1],
                    "calls": 0,
                    "values": [],
                    "unreported_calls": 0,
                },
            )
            count = token_count(record.usage)
            group["calls"] += 1
            group["values"].append(count)
            if count is None:
                group["unreported_calls"] += 1
        model_usage = []
        for group in groups.values():
            group["tokens"] = sum_known(group.pop("values"))
            model_usage.append(group)
        result["model_usage"] = sorted(
            model_usage, key=lambda r: (-r["calls"], r["provider_code"], r["model_code"])
        )
        result["model_calls"] = len(usage)
        result["tokens"] = sum_known(g["tokens"] for g in model_usage)
        result["unreported_calls"] = sum(g["unreported_calls"] for g in model_usage)
    return result


def action_name(action):
    method, _, path = action.partition(" ")
    endings = {
        "/cancel": "取消任务",
        "/retry": "重新执行任务",
        "/rerun": "重新执行成功任务",
        "/messages": "继续任务对话",
        "/rollback": "回滚版本",
        "/versions": "发布版本",
        "/password": "修改或重置密码",
        "/check": "检测 MCP",
        "/grant": "修改资源授权",
    }
    for ending, label in endings.items():
        if path.endswith(ending):
            return label
    names = {
        "agents": "Agent",
        "tasks": "任务",
        "model-profiles": "模型",
        "tools": "工具",
        "mcps": "MCP",
        "skills": "Skill",
        "users": "用户",
        "members": "租户成员",
        "roles": "角色",
        "tenants": "租户",
    }
    parts = path.removeprefix("/api/v1/").split("/")
    return {"POST": "创建/保存", "PATCH": "更新", "PUT": "保存", "DELETE": "移除"}.get(
        method, method
    ) + names.get(parts[0], "配置")
