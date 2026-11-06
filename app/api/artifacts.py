from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.api import auth
from app.api.platform import Identity, Session
from app.core.config import get_settings
from app.services.artifacts import artifact_page, download, file_chunks, upload

artifacts_router = APIRouter(prefix="/api/v1/tasks", tags=["artifacts"])


@artifacts_router.get("/{code}/artifacts")
async def list_artifacts(
    code: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    principal=Identity,
    session=Session,
):
    return await artifact_page(principal, session, code, limit, offset, get_settings())


@artifacts_router.post("/{code}/artifacts", status_code=201)
async def upload_artifact(
    code: str,
    request: Request,
    file_name: str = Query(min_length=1, max_length=255),
    idempotency_key: str = Header(min_length=36, max_length=36),
    principal=Identity,
):
    try:
        retry_key = str(UUID(idempotency_key))
    except ValueError:
        raise HTTPException(422, "请提供有效的上传重试标识") from None
    return await upload(
        request,
        principal,
        auth.session_factory,
        code,
        file_name,
        retry_key,
        get_settings(),
    )


@artifacts_router.get("/{code}/artifacts/{artifact_code}/download")
async def download_artifact(code: str, artifact_code: str, request: Request, principal=Identity):
    source, content = await download(
        request,
        principal,
        auth.session_factory,
        code,
        artifact_code,
        get_settings(),
    )
    return StreamingResponse(
        file_chunks(source),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename=artifact; filename*=UTF-8''"
            + quote(content["file_name"], safe=""),
            "Content-Length": str(content["size_bytes"]),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Artifact-SHA256": content["sha256"],
        },
        background=BackgroundTask(source.close),
    )


@artifacts_router.get("/{code}/inputs/{index}/download")
async def download_input(code: str, index: int, principal=Identity, session=Session):
    import base64
    import hashlib

    from fastapi.responses import Response

    from app.api.platform import scope
    from app.models.platform import AgentTask

    repo = await scope(principal, session, permission="tasks.read")
    task = await repo.get(AgentTask, "task_code", code)
    files = task.input.get("attachments", [])
    if not 0 <= index < len(files):
        raise HTTPException(404, "附件不存在")
    file = files[index]
    raw = base64.b64decode(file["content_base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != file["sha256"]:
        raise HTTPException(409, "附件完整性校验失败")
    return Response(
        raw,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename=input; filename*=UTF-8''"
            + quote(file["file_name"], safe=""),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Artifact-SHA256": file["sha256"],
        },
    )
