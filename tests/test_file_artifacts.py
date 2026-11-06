import hashlib
import io
import uuid
from dataclasses import replace

import boto3
import pytest
import pytest_asyncio
from botocore.response import StreamingBody
from botocore.stub import Stubber
from sqlalchemy import select, update
from test_platform_v1 import Gateway, configured_agent, queued

from app.api import artifacts, auth
from app.core.config import Settings
from app.core.identity import LOCAL_TENANT, LOCAL_USER, Principal, new_code
from app.models.platform import AgentTask, TaskArtifact, Tenant
from app.repositories.task_queue import TaskQueue
from app.runtime.agent import AgentRuntime
from app.services.object_storage import LocalObjects, ObjectMissing, S3Objects, StorageError
from app.workers.agent_worker import process_lease

pytest_plugins = ["test_platform_v1"]


@pytest_asyncio.fixture
async def storage(client, monkeypatch, tmp_path):
    settings = auth.get_settings().model_copy(
        update={"artifact_local_dir": str(tmp_path / "objects")}
    )
    monkeypatch.setattr(artifacts, "get_settings", lambda: settings)
    return settings


async def success(client, db):
    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    queue = TaskQueue(db, "files", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway()), await queue.claim())
    return task["task_code"]


async def upload(client, task, data=b"\x00\xff\x01binary\n", name="报告.bin", key=None):
    return await client.post(
        f"/api/v1/tasks/{task}/artifacts",
        params={"file_name": name},
        content=data,
        headers={
            "Idempotency-Key": key or str(uuid.uuid4()),
            "Content-Type": "application/octet-stream",
        },
    )


def download_url(task, artifact):
    return f"/api/v1/tasks/{task}/artifacts/{artifact}/download"


@pytest.mark.asyncio
async def test_binary_roundtrip_metadata_filename_pagination_and_idempotency(client, db, storage):
    task = await success(client, db)
    key = str(uuid.uuid4())
    content = bytes(range(256)) * 512
    response = await upload(client, task, content, '../../长文件:"报告".bin', key)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["artifact_type"] == "FILE" and row["content"]["source"] == "UPLOAD"
    assert "storage" not in row["content"] and "retry_key" not in row["content"]
    assert row["content"]["size_bytes"] == len(content)
    assert row["content"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert "/" not in row["content"]["file_name"] and not row["content"]["file_name"].startswith(
        "."
    )
    repeated = await upload(client, task, content, '../../长文件:"报告".bin', key)
    assert repeated.json()["artifact_code"] == row["artifact_code"]
    assert (
        await upload(client, task, b"different", '../../长文件:"报告".bin', key)
    ).status_code == 409
    assert len(list(LocalObjects(storage).root.rglob("art_*"))) == 1
    result = await client.get(download_url(task, row["artifact_code"]))
    assert result.content == content
    assert result.headers["content-type"] == "application/octet-stream"
    assert "attachment" in result.headers["content-disposition"]
    assert "filename*=UTF-8''" in result.headers["content-disposition"]
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.headers["cache-control"] == "no-store"
    page = (await client.get(f"/api/v1/tasks/{task}/artifacts?limit=1")).json()
    second = (await client.get(f"/api/v1/tasks/{task}/artifacts?limit=1&offset=1")).json()
    assert page["total"] == 2 and len(page["items"]) == len(second["items"]) == 1
    assert page["items"][0]["artifact_code"] != second["items"][0]["artifact_code"]
    detail = await client.get(f"/api/v1/tasks/{task}")
    assert "storage" not in detail.text and "retry_key" not in detail.text
    assert (
        await client.get(download_url(task, second["items"][0]["artifact_code"]))
    ).status_code == 404


@pytest.mark.asyncio
async def test_empty_oversized_and_chunked_uploads_do_not_create_objects(client, db, storage):
    task = await success(client, db)
    storage.artifact_max_file_bytes = 4
    assert (await upload(client, task, b"")).status_code == 422
    assert (await upload(client, task, b"12345")).status_code == 413

    async def chunks():
        yield b"12"
        yield b"345"

    assert (await upload(client, task, chunks())).status_code == 413
    assert (await upload(client, task, b"1234")).status_code == 201
    assert len(list(LocalObjects(storage).root.rglob("art_*"))) == 1


@pytest.mark.asyncio
async def test_file_access_is_scoped_and_disabled_tenant_is_readonly(client, db, storage):
    task = await success(client, db)
    row = (await upload(client, task)).json()
    other = new_code("ten")
    async with db() as session:
        session.add(Tenant(tenant_code=other, tenant_name="other"))
        await session.commit()
    path = download_url(task, row["artifact_code"])
    assert (await client.get(path, headers={"X-Tenant-Code": other})).status_code == 404
    assert (
        await client.get(f"/api/v1/tasks/{task}/artifacts", headers={"X-Tenant-Code": other})
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/tasks/{task}/artifacts?file_name=x",
            content=b"x",
            headers={"X-Tenant-Code": other, "Idempotency-Key": str(uuid.uuid4())},
        )
    ).status_code == 404
    async with db() as session:
        await session.execute(
            update(Tenant).where(Tenant.tenant_code == LOCAL_TENANT).values(status=0)
        )
        await session.commit()
    assert (await client.get(path)).status_code == 200
    assert (await upload(client, task)).status_code == 403


@pytest.mark.asyncio
async def test_permissions_ownership_task_state_and_run_link(client, db, storage):
    task = await success(client, db)
    row = (await upload(client, task)).json()
    app = client._transport.app
    principal = Principal(LOCAL_USER, "local", role="CUSTOM", permissions=("tasks.read",))
    app.dependency_overrides[auth.current_principal] = lambda: principal
    assert (await client.get(download_url(task, row["artifact_code"]))).status_code == 200
    assert (await upload(client, task)).status_code == 403
    principal = replace(principal, permissions=())
    assert (await client.get(download_url(task, row["artifact_code"]))).status_code == 403
    principal = replace(
        principal, user_code=new_code("usr"), permissions=("tasks.read", "tasks.create")
    )
    assert (await upload(client, task)).status_code == 403
    principal = replace(principal, permissions=("tasks.read", "tasks.create", "tasks.manage"))
    assert (await upload(client, task)).status_code == 201
    app.dependency_overrides.clear()
    async with db() as session:
        await session.execute(
            update(AgentTask).where(AgentTask.task_code == task).values(status="RUNNING")
        )
        await session.commit()
    assert (await upload(client, task)).status_code == 409


@pytest.mark.asyncio
async def test_corruption_missing_object_and_wrong_task_never_return_file(client, db, storage):
    task = await success(client, db)
    row = (await upload(client, task)).json()
    path = next(LocalObjects(storage).root.rglob("art_*"))
    path.write_bytes(b"tampered!")
    failed = await client.get(download_url(task, row["artifact_code"]))
    assert failed.status_code == 503 and b"tampered!" not in failed.content
    path.unlink()
    assert (await client.get(download_url(task, row["artifact_code"]))).status_code == 404
    other = await success(client, db)
    assert (await client.get(download_url(other, row["artifact_code"]))).status_code == 404


@pytest.mark.asyncio
async def test_task_changes_during_upload_compensate_object(client, db, storage, monkeypatch):
    task = await success(client, db)
    from app.services import artifacts as service

    original = service.revalidate

    async def changed(*args):
        async with db() as session:
            await session.execute(
                update(AgentTask).where(AgentTask.task_code == task).values(status="PENDING")
            )
            await session.commit()
        return await original(*args)

    monkeypatch.setattr(service, "revalidate", changed)
    response = await upload(client, task)
    assert response.status_code == 409
    assert not list(LocalObjects(storage).root.rglob("art_*"))
    async with db() as session:
        assert not await session.scalar(
            select(TaskArtifact.id).where(TaskArtifact.artifact_type == "FILE")
        )


@pytest.mark.asyncio
async def test_session_revocation_during_file_transfer_compensates_upload(
    client, db, storage, monkeypatch
):
    task = await success(client, db)
    from fastapi import HTTPException

    from app.services import artifacts as service

    async def revoked(*args):
        raise HTTPException(401, "登录已失效")

    monkeypatch.setattr(service, "revalidate", revoked)
    assert (await upload(client, task)).status_code == 401
    assert not list(LocalObjects(storage).root.rglob("art_*"))


def key():
    return f"tenant/{new_code('ten')}/task/{new_code('tsk')}/{new_code('art')}"


def test_local_objects_are_immutable_and_reject_path_traversal_symlinks(tmp_path):
    settings = Settings(_env_file=None, artifact_local_dir=str(tmp_path / "objects"))
    store, object_key = LocalObjects(settings), key()
    content = b"binary"
    digest = hashlib.sha256(content).hexdigest()
    reference = store.put(object_key, io.BytesIO(content), len(content), digest)
    with pytest.raises(StorageError):
        store.put(object_key, io.BytesIO(content), len(content), digest)
    with pytest.raises(StorageError):
        store.put("../../unsafe", io.BytesIO(content), len(content), digest)
    target = io.BytesIO()
    store.read(reference, target, len(content), digest)
    assert target.read() == content
    store.path(object_key).unlink()
    store.path(object_key).symlink_to(tmp_path / "outside")
    with pytest.raises(StorageError):
        store.read(reference, target, len(content), digest)
    with pytest.raises(StorageError):
        store.read(
            {"key": object_key.replace("tenant/", "tenant/../", 1)}, target, len(content), digest
        )


def test_s3_sdk_parameters_integrity_missing_error_and_redaction():
    settings = Settings(
        _env_file=None, artifact_storage_backend="s3", artifact_s3_bucket="private-files"
    )
    sdk = boto3.client(
        "s3",
        aws_access_key_id="dummy-access",
        aws_secret_access_key="dummy-secret",
        region_name="us-east-1",
    )
    store = S3Objects(settings, sdk)
    object_key, data = key(), b"\x00s3\xff"
    digest, source = hashlib.sha256(data).hexdigest(), io.BytesIO(data)
    with Stubber(sdk) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "private-files",
                "Key": object_key,
                "Body": source,
                "ContentLength": len(data),
                "ContentType": "application/octet-stream",
                "IfNoneMatch": "*",
                "Metadata": {"sha256": digest},
            },
        )
        reference = store.put(object_key, source, len(data), digest)
        body = StreamingBody(io.BytesIO(data), len(data))
        stub.add_response(
            "get_object",
            {"Body": body, "ContentLength": len(data)},
            {"Bucket": "private-files", "Key": object_key},
        )
        target = io.BytesIO()
        store.read(reference, target, len(data), digest)
        assert target.read() == data
        stub.add_client_error("get_object", "NoSuchKey", "do-not-echo", http_status_code=404)
        with pytest.raises(ObjectMissing, match="不存在"):
            store.read(reference, target, len(data), digest)
        stub.add_client_error("put_object", "AccessDenied", "dummy-secret", http_status_code=403)
        with pytest.raises(StorageError) as exc:
            store.put(object_key, io.BytesIO(data), len(data), digest)
        assert "dummy-secret" not in str(exc.value)
        with pytest.raises(StorageError, match="配置已变更"):
            store.read({**reference, "bucket": "another"}, target, len(data), digest)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "values",
    [
        {"artifact_local_dir": "relative"},
        {"artifact_storage_backend": "s3"},
        {"artifact_s3_endpoint": "https://user:password@bucket.test"},
        {"artifact_s3_endpoint": "http://storage.test", "environment": "prod"},
        {"artifact_s3_access_key": "dummy"},
    ],
)
def test_storage_config_rejects_incomplete_or_unsafe_settings(values):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_s3_real_http_sdk_roundtrip_and_cleanup(client, db, storage):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    objects, calls = {}, []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def reply(self, status, body=b""):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(("PUT", self.path))
            assert self.headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")
            assert self.headers["If-None-Match"] == "*"
            assert self.headers["x-amz-meta-sha256"] == hashlib.sha256(body).hexdigest()
            if self.path in objects:
                return self.reply(412, b"<Error><Code>PreconditionFailed</Code></Error>")
            objects[self.path] = body
            self.reply(200)

        def do_GET(self):
            calls.append(("GET", self.path))
            self.reply(200, objects[self.path]) if self.path in objects else self.reply(
                404, b"<Error><Code>NoSuchKey</Code></Error>"
            )

        def do_DELETE(self):
            calls.append(("DELETE", self.path))
            objects.pop(self.path, None)
            self.reply(204)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from pydantic import SecretStr

        storage.artifact_storage_backend = "s3"
        storage.artifact_s3_bucket = "isolated-files"
        storage.artifact_s3_endpoint = f"http://127.0.0.1:{server.server_port}"
        storage.artifact_s3_access_key = SecretStr("dummy-access")
        storage.artifact_s3_secret_key = SecretStr("dummy-secret")
        task = await success(client, db)
        data = bytes(range(256)) * 300
        retry = str(uuid.uuid4())
        response = await upload(client, task, data, key=retry)
        assert response.status_code == 201, response.text
        artifact = response.json()["artifact_code"]
        assert (await client.get(download_url(task, artifact))).content == data
        assert (await upload(client, task, data, key=retry)).json()["artifact_code"] == artifact
        assert len(objects) == 1 and sum(method == "DELETE" for method, _ in calls) == 1
        assert all(
            path.startswith(f"/isolated-files/tenant/{LOCAL_TENANT}/task/{task}/")
            for _, path in calls
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_database_flush_failure_compensates_unattached_object(client, db, storage):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError

    task = await success(client, db)

    def fail(_mapper, _connection, target):
        if target.artifact_type == "FILE":
            raise SQLAlchemyError("private-database-details")

    event.listen(TaskArtifact, "before_insert", fail)
    try:
        response = await upload(client, task)
        assert response.status_code == 503
        assert "private-database-details" not in response.text
        assert not list(LocalObjects(storage).root.rglob("art_*"))
    finally:
        event.remove(TaskArtifact, "before_insert", fail)


@pytest.mark.asyncio
async def test_cancelled_upload_waits_for_storage_thread_then_compensates(
    client, db, storage, monkeypatch
):
    import asyncio
    import threading

    task = await success(client, db)
    started, release = threading.Event(), threading.Event()
    original = LocalObjects.put

    def paused(*args):
        result = original(*args)
        started.set()
        assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(LocalObjects, "put", paused)
    pending = asyncio.create_task(upload(client, task))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        pending.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not list(LocalObjects(storage).root.rglob("art_*"))
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_same_retry_key_creates_only_one_file(client, db, storage):
    import asyncio

    task = await success(client, db)
    retry = str(uuid.uuid4())
    first, second = await asyncio.gather(
        upload(client, task, key=retry), upload(client, task, key=retry)
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["artifact_code"] == second.json()["artifact_code"]
    assert len(list(LocalObjects(storage).root.rglob("art_*"))) == 1
