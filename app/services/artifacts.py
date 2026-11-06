"""Append-only attachments; object locations never enter public task responses."""

import asyncio
import hashlib
import logging
import re
import tempfile
import unicodedata

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.identity import new_code
from app.models.platform import AgentRun, AgentTask, TaskArtifact
from app.repositories.platform import public
from app.services.object_storage import CHUNK, ObjectMissing, StorageError, object_store
from app.services.permissions import allowed, require
from app.services.task_stream import revalidate

logger = logging.getLogger(__name__)


def filename(value):
    value = unicodedata.normalize("NFKC", value)
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C"))
    return re.sub(r'[\\/:*?"<>|]', "_", value).strip(" .")[:128].rstrip(" .") or "文件产物"


def artifact_public(row):
    value = public(row)
    if row.artifact_type == "FILE":
        value["content"] = {
            k: row.content.get(k)
            for k in (
                "file_name",
                "size_bytes",
                "sha256",
                "uploaded_by",
                "source",
            )
        }
    return value


async def io(function, *args):
    # Cancellation must not close/unlink a spool while its storage thread is still using it.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def upload_scope(principal, session, code, lock=False):
    from app.api.platform import scope

    require(principal, "tasks.read")
    require(principal, "tasks.create")
    repo = await scope(principal, session, write=True)
    task = await repo.get(AgentTask, "task_code", code, lock=lock)
    if task.created_by != principal.user_code and not allowed(principal, "tasks.manage"):
        raise HTTPException(403, "只能为自己的任务上传文件")
    if task.status != "SUCCEEDED":
        raise HTTPException(409, "仅成功任务可以追加文件产物")
    run = await repo.get(AgentRun, "run_code", (task.output or {}).get("run_code", ""))
    if run.task_code != code or run.status != "SUCCEEDED" or run.parent_run_code:
        raise HTTPException(409, "任务没有可关联的成功运行")
    return repo, task, run


async def artifact_page(principal, session, code, limit, offset, settings):
    from app.api.platform import scope

    repo = await scope(principal, session, permission="tasks.read")
    await repo.get(AgentTask, "task_code", code)
    query = repo.query(TaskArtifact).where(TaskArtifact.task_code == code)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.scalars(
            query.order_by(TaskArtifact.created_at.desc(), TaskArtifact.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "items": [artifact_public(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "max_file_bytes": settings.artifact_max_file_bytes,
    }


async def upload(request, principal, factory, code, name, retry_key, settings):
    async with factory() as session:
        _, _, run = await upload_scope(principal, session, code)
        source_run, version_no = run.run_code, run.turn_no
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise HTTPException(400, "文件长度无效") from None
        if length < 0:
            raise HTTPException(400, "文件长度无效")
        if length > settings.artifact_max_file_bytes:
            raise HTTPException(413, "文件超过允许的大小")
    artifact_code = new_code("art")
    key = f"tenant/{principal.tenant_code}/task/{code}/{artifact_code}"
    stored = None
    committed = False
    commit_started = False
    store = None
    with tempfile.TemporaryFile() as source:
        size, digest = 0, hashlib.sha256()
        try:
            # No multipart parser or whole-request buffer; enforce bounds on actual bytes.
            async with asyncio.timeout(120):
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.artifact_max_file_bytes:
                        raise HTTPException(413, "文件超过允许的大小")
                    digest.update(chunk)
                    await io(source.write, chunk)
            if not size:
                raise HTTPException(422, "不能上传空文件")
            if declared is not None and size != int(declared):
                raise HTTPException(400, "文件长度不匹配")
            source.seek(0)
            store = object_store(settings)

            def persist():
                nonlocal stored
                stored = store.put(key, source, size, digest.hexdigest())

            await io(persist)
            fresh = await revalidate(request, principal, factory)
            async with factory() as session:
                repo, _, run = await upload_scope(fresh, session, code, lock=True)
                if run.run_code != source_run:
                    raise HTTPException(409, "任务轮次已变化，请刷新后重新上传")
                existing = await session.scalar(
                    repo.query(TaskArtifact).where(
                        TaskArtifact.task_code == code,
                        TaskArtifact.artifact_type == "FILE",
                        TaskArtifact.content["retry_key"].astext == retry_key,
                    )
                )
                if existing:
                    if (
                        existing.content["sha256"] != digest.hexdigest()
                        or existing.content["file_name"] != filename(name)
                        or existing.run_code != source_run
                    ):
                        raise HTTPException(409, "上传重试标识已用于其他文件")
                    return artifact_public(existing)
                row = TaskArtifact(
                    tenant_code=principal.tenant_code,
                    artifact_code=artifact_code,
                    task_code=code,
                    run_code=source_run,
                    artifact_type="FILE",
                    artifact_name=filename(name),
                    version_no=version_no,
                    content={
                        "file_name": filename(name),
                        "size_bytes": size,
                        "sha256": digest.hexdigest(),
                        "storage": stored,
                        "source": "UPLOAD",
                        "uploaded_by": principal.user_code,
                        "retry_key": retry_key,
                    },
                )
                session.add(row)
                await session.flush()
                commit_started = True
                await session.commit()
                committed = True
                return artifact_public(row)
        except TimeoutError:
            raise HTTPException(408, "上传超时，请重试") from None
        except StorageError as exc:
            raise HTTPException(503, str(exc)) from None
        finally:
            if stored and not committed:
                # A lost commit acknowledgement must never delete an attached object.
                if commit_started:
                    try:
                        async with factory() as session:
                            committed = bool(
                                await session.scalar(
                                    select(TaskArtifact.id).where(
                                        TaskArtifact.artifact_code == artifact_code,
                                        TaskArtifact.tenant_code == principal.tenant_code,
                                    )
                                )
                            )
                    except Exception:
                        committed = True  # Retain for reconciliation if the DB is unavailable.
                        logger.warning("文件提交状态待核对：%s", artifact_code)
                if not committed:
                    try:
                        await io(store.delete, stored)
                    except (StorageError, OSError):
                        logger.warning("文件对象补偿删除待核对：%s", artifact_code)
            if store:
                await io(store.close)


async def download(request, principal, factory, code, artifact_code, settings):
    from app.api.platform import scope

    async with factory() as session:
        repo = await scope(principal, session, permission="tasks.read")
        await repo.get(AgentTask, "task_code", code)
        artifact = await repo.get(TaskArtifact, "artifact_code", artifact_code)
        if artifact.task_code != code or artifact.artifact_type != "FILE":
            raise HTTPException(404, "文件产物不存在")
        content = artifact.content
    source = tempfile.TemporaryFile()
    store = None
    try:
        reference = content["storage"]
        expected = f"tenant/{principal.tenant_code}/task/{code}/{artifact_code}"
        if reference.get("key") != expected:
            raise StorageError("文件对象标识无效")
        store = object_store(settings, reference.get("backend"))
        await io(store.read, reference, source, content["size_bytes"], content["sha256"])
        await revalidate(request, principal, factory)
        return source, content
    except ObjectMissing as exc:
        source.close()
        raise HTTPException(404, str(exc)) from None
    except StorageError as exc:
        source.close()
        raise HTTPException(503, str(exc)) from None
    except BaseException:
        source.close()
        raise
    finally:
        if store:
            await io(store.close)


def file_chunks(source):
    try:
        while chunk := source.read(CHUNK):
            yield chunk
    finally:
        source.close()
