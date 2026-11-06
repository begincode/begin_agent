"""Publish generated files with task success; compensate proven orphans."""

import asyncio
import base64
import hashlib
import io
import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.identity import new_code
from app.models.platform import TaskArtifact
from app.services.artifacts import io as storage_io
from app.services.object_storage import object_store

logger = logging.getLogger(__name__)


async def finish_generated(queue, lease, output):
    files = output.get("_generated_files", [])
    if not files:
        return await queue.finish(lease, "SUCCEEDED", output)
    clean = {k: v for k, v in output.items() if k != "_generated_files"}
    store = object_store(get_settings())
    rows, references = [], []
    try:
        if len(files) > 3:
            raise ValueError("生成文件数量超过限制")
        for file in files:
            raw = base64.b64decode(file["content_base64"], validate=True)
            if not 0 < len(raw) <= 32768:
                raise ValueError("生成文件大小超过限制")
            code = new_code("art")
            key = f"tenant/{lease.tenant_code}/task/{lease.task_code}/{code}"
            reference = {"backend": store.backend, "key": key}
            references.append(reference)
            digest = hashlib.sha256(raw).hexdigest()
            await storage_io(store.put, key, io.BytesIO(raw), len(raw), digest)
            rows.append(
                TaskArtifact(
                    tenant_code=lease.tenant_code,
                    artifact_code=code,
                    task_code=lease.task_code,
                    run_code=file["run_code"],
                    artifact_type="FILE",
                    artifact_name=file["file_name"],
                    version_no=lease.turn_no,
                    content={
                        "file_name": file["file_name"],
                        "size_bytes": len(raw),
                        "sha256": digest,
                        "source": "GENERATED",
                        "uploaded_by": None,
                        "storage": reference,
                    },
                )
            )
        await queue.finish(lease, "SUCCEEDED", clean, artifacts=rows)
    except BaseException:
        # A commit can succeed before its acknowledgment fails. Query before compensating.
        try:
            async with queue.factory() as session:
                persisted = (
                    await session.scalar(
                        select(func.count())
                        .select_from(TaskArtifact)
                        .where(
                            TaskArtifact.tenant_code == lease.tenant_code,
                            TaskArtifact.artifact_code.in_([row.artifact_code for row in rows]),
                        )
                    )
                    if rows
                    else 0
                )
            if not persisted:
                for reference in references:
                    try:
                        await storage_io(store.delete, reference)
                    except Exception:
                        logger.error("Generated object compensation failed; reconcile references")
        except BaseException:
            logger.error("Generated object commit uncertain; objects retained for reconciliation")
        raise
    finally:
        await asyncio.to_thread(store.close)
