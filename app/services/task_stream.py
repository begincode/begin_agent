"""Cross-process SSE reads cumulative PostgreSQL run previews, not model connections."""

import asyncio
import hashlib
import json
import time

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.identity import tenant_context
from app.models.platform import AgentRun, AgentTask
from app.models.workspace import TaskTurn
from app.services.permissions import allowed, require

POLL_SECONDS = 0.3
REAUTH_SECONDS = 1.5
CONNECTION_SECONDS = 20


def frame(event, value, revision=None):
    return (
        (f"id: {revision}\n" if revision else "")
        + f"event: {event}\ndata: "
        + json.dumps(jsonable_encoder(value), ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )


async def snapshot(factory, principal, code):
    from app.api.platform import scope

    require(principal, "tasks.read")
    ctx = tenant_context.set(principal.tenant_code)
    try:
        async with factory() as session:
            repo = await scope(principal, session)
            task = await repo.get(AgentTask, "task_code", code)
            turn = await session.scalar(
                repo.query(TaskTurn)
                .where(TaskTurn.task_code == code)
                .order_by(TaskTurn.turn_no.desc())
                .limit(1)
            )
            turn_no = turn.turn_no if turn else 1
            run = await session.scalar(
                repo.query(AgentRun)
                .where(
                    AgentRun.task_code == code,
                    AgentRun.turn_no == turn_no,
                    AgentRun.parent_run_code.is_(None),
                )
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .limit(1)
            )
            if task.status == "PENDING" or (
                task.status == "RUNNING" and run and run.lease_token != task.lease_token
            ):
                run = None
            activity = []
            if run and allowed(principal, "runs.read"):
                rows = (
                    await session.execute(
                        select(
                            AgentRun.run_code,
                            AgentRun.parent_run_code,
                            AgentRun.status,
                            AgentRun.output["live"]["phase"].label("phase"),
                        )
                        .where(
                            AgentRun.tenant_code == principal.tenant_code,
                            AgentRun.root_run_code == run.run_code,
                        )
                        .order_by(AgentRun.created_at, AgentRun.id)
                        .limit(100)
                    )
                ).all()
                activity = [
                    {
                        "run_code": r.run_code,
                        "child": bool(r.parent_run_code),
                        "status": r.status,
                        "phase": r.phase,
                    }
                    for r in rows
                ]
            value = {
                "task_code": code,
                "turn_no": turn_no,
                "status": task.status,
                "retry_count": task.retry_count,
                "error_code": task.error_code,
                "run_code": run.run_code if run else None,
                "preview": (run.output or {}).get("live") if run else None,
                "activity": activity,
                "terminal": task.status not in {"PENDING", "RUNNING"},
            }
            revision = hashlib.sha256(
                json.dumps(jsonable_encoder(value), sort_keys=True).encode()
            ).hexdigest()[:24]
            return {**value, "revision": revision}
    finally:
        tenant_context.reset(ctx)


async def revalidate(request, principal, factory):
    from app.api import auth

    settings = auth.get_settings()
    if settings.auth_mode == "local":
        return principal
    if settings.auth_mode == "password":
        from app.api.accounts import authenticate

        external = await authenticate(request, factory)
    else:
        token = request.headers.get("authorization", "").removeprefix(
            "Bearer "
        ) or request.cookies.get(auth.COOKIE, "")
        external = await auth.get_sso_remote().validate(token)
    fresh = await auth.resolve_identity(external, principal.tenant_code)
    if fresh.user_code != principal.user_code:
        raise HTTPException(401, "登录身份已变化")
    require(fresh, "tasks.read")
    return fresh


async def events(request, factory, principal, code, initial):
    started = last_auth = last_beat = time.monotonic()
    previous = None
    value = initial
    while not await request.is_disconnected():
        now = time.monotonic()
        try:
            if now - last_auth >= REAUTH_SECONDS:
                principal = await revalidate(request, principal, factory)
                last_auth = now
            # Re-read after revalidation so revoked trace privileges cannot leak activity.
            if previous is not None:
                value = await snapshot(factory, principal, code)
            if value["revision"] != previous:
                yield frame("snapshot", value, value["revision"])
                previous = value["revision"]
            if value["terminal"]:
                yield frame("complete", {"status": value["status"]})
                return
            if now - started >= CONNECTION_SECONDS:
                yield frame("reconnect", {})
                return
            if now - last_beat >= 5:
                yield ": heartbeat\n\n"
                last_beat = now
            await asyncio.sleep(POLL_SECONDS)
        except HTTPException as exc:
            yield frame(
                "access_error",
                {"status": exc.status_code, "message": "实时输出访问已失效，请刷新或重新登录"},
            )
            return
        except SQLAlchemyError:
            yield frame("unavailable", {"message": "实时输出暂不可用，正在恢复"})
            return


async def turn_previews(session, repo, task):
    """Return only parent-run drafts, including older failed turns; no child payloads."""
    rows = (
        await session.execute(
            select(AgentRun.turn_no, AgentRun.lease_token, AgentRun.output["live"].label("preview"))
            .where(
                AgentRun.tenant_code == repo.tenant_code,
                AgentRun.task_code == task.task_code,
                AgentRun.parent_run_code.is_(None),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        )
    ).all()
    result = {}
    for row in rows:
        if row.turn_no not in result:
            result[row.turn_no] = (row.lease_token, row.preview)
    return result
