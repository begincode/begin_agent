import asyncio
from dataclasses import replace

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from test_platform_v1 import configured_agent, queued

from app.core.identity import LOCAL_TENANT, Principal
from app.services.snapshot_cache import SnapshotCache
from app.services.task_stream import snapshot

pytest_plugins = ["test_platform_v1"]
READER = Principal(
    "reader",
    "reader",
    tenant_code=LOCAL_TENANT,
    role="CUSTOM",
    permissions=("tasks.read", "runs.read"),
)


def value(code="task", terminal=False, content="preview"):
    return {
        "task_code": code,
        "terminal": terminal,
        "revision": "one",
        "preview": {"content": content},
    }


async def test_concurrent_viewers_share_one_read_and_expired_values_refresh():
    calls, release = [], asyncio.Event()

    async def read(factory, principal, code):
        calls.append(code)
        await release.wait()
        return value(code)

    cache = SnapshotCache(loader=read)
    factory = object()
    jobs = [asyncio.create_task(cache.get(factory, READER, "task")) for _ in range(30)]
    await asyncio.sleep(0.01)
    assert len(calls) == 1
    release.set()
    results = await asyncio.gather(*jobs)
    assert len(results) == 30 and all(r["revision"] == "one" for r in results)
    await cache.get(factory, READER, "task")
    assert len(calls) == 1
    # Make this exact entry expired without sleeping or altering the event-loop clock.
    key = next(iter(cache.values))
    cache.values[key] = (0, cache.values[key][1])
    await cache.get(factory, READER, "task")
    assert len(calls) == 2
    await cache.close()
    assert not cache.values and not cache.pending


async def test_cache_separates_tenants_trace_capability_and_database_factories():
    calls = []

    async def read(factory, principal, code):
        calls.append((factory, principal.tenant_code, principal.permissions))
        return value(code)

    cache = SnapshotCache(loader=read)
    factory = object()
    await cache.get(factory, READER, "task")
    await cache.get(factory, replace(READER, tenant_code="other"), "task")
    await cache.get(factory, replace(READER, permissions=("tasks.read",)), "task")
    await cache.get(object(), READER, "task")
    assert len(calls) == 4
    with pytest.raises(HTTPException) as exc:
        await cache.get(factory, replace(READER, permissions=()), "task")
    assert exc.value.status_code == 403 and len(calls) == 4
    await cache.close()


async def test_terminal_large_and_excess_entries_do_not_accumulate():
    async def read(_factory, _principal, code):
        return value(
            code, terminal=code == "done", content="x" * 1000 if code == "large" else "preview"
        )

    cache = SnapshotCache(loader=read, max_entries=2, max_bytes=200)
    factory = object()
    for code in ["one", "two", "three", "done", "large"]:
        await cache.get(factory, READER, code)
    assert len(cache.values) == 2
    assert all(key[2] in {"two", "three"} for key in cache.values)
    await cache.close()


async def test_disconnecting_one_viewer_keeps_other_viewer_read_alive():
    started, release = asyncio.Event(), asyncio.Event()

    async def read(*args):
        started.set()
        await release.wait()
        return value()

    cache = SnapshotCache(loader=read)
    factory = object()
    first = asyncio.create_task(cache.get(factory, READER, "task"))
    second = asyncio.create_task(cache.get(factory, READER, "task"))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not second.done()
    release.set()
    assert (await second)["revision"] == "one"
    await cache.close()


async def test_application_shutdown_cancels_inflight_reads_and_errors_are_not_cached():
    started, closed = asyncio.Event(), asyncio.Event()

    async def blocked(*args):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    cache = SnapshotCache(loader=blocked)
    pending = asyncio.create_task(cache.get(object(), READER, "task"))
    await started.wait()
    await cache.close()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert closed.is_set() and not cache.pending
    calls = []

    async def failed(*args):
        calls.append(1)
        raise HTTPException(404, "missing")

    cache = SnapshotCache(loader=failed)
    factory = object()
    for _ in range(2):
        with pytest.raises(HTTPException):
            await cache.get(factory, READER, "missing")
    assert len(calls) == 2 and not cache.pending and not cache.values
    await cache.close()


async def test_snapshot_projects_only_live_fields_and_task_lookup_remains_scoped(client, db):
    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    engine = db.kw["bind"]
    queries = []

    def capture(_conn, _cursor, statement, *_args):
        queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        state = await snapshot(db, READER, task["task_code"])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert state["status"] == "PENDING" and state["preview"] is None
    assert not any("agent_task.input" in q or "agent_task.output" in q for q in queries)
    assert not any("agent_run.output AS agent_run_output" in q for q in queries)
    with pytest.raises(HTTPException) as exc:
        await snapshot(db, replace(READER, tenant_code="other"), task["task_code"])
    assert exc.value.status_code in {403, 404}


async def test_existing_identity_revalidation_is_readonly_and_still_reads_current_membership(
    client, db, monkeypatch
):
    from sqlalchemy import update

    from app.api import auth
    from app.core.identity import LOCAL_USER
    from app.models.platform import PlatformUser

    monkeypatch.setattr(auth, "session_factory", db)
    engine = db.kw["bind"]
    queries = []

    def capture(_conn, _cursor, statement, *_args):
        queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        principal = await auth.resolve_identity("local", LOCAL_TENANT)
        assert principal.user_code == LOCAL_USER
        assert not any(q.lstrip().upper().startswith("INSERT") for q in queries)
        async with db() as session:
            await session.execute(
                update(PlatformUser).where(PlatformUser.user_code == LOCAL_USER).values(status=0)
            )
            await session.commit()
        with pytest.raises(HTTPException) as exc:
            await auth.resolve_identity("local", LOCAL_TENANT)
        assert exc.value.status_code == 403
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
