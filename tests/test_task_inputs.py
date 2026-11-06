import base64
import hashlib

import pytest
from test_platform_v1 import Gateway, configured_agent

from app.repositories.task_queue import TaskQueue
from app.runtime.agent import AgentRuntime
from app.workers.agent_worker import process_lease

pytest_plugins = ["test_platform_v1"]


def file(name="参考.md", raw="参考内容：销售额 123".encode()):
    return {"file_name": name, "content_base64": base64.b64encode(raw).decode()}


@pytest.mark.asyncio
async def test_input_roundtrip_idempotency_download_and_model_reference(client, db):
    agent, _ = await configured_agent(client)
    payload = {
        "agent_code": agent["agent_code"],
        "title": "附件",
        "prompt": "根据参考回答",
        "idempotency_key": "inputs-key",
        "attachments": [file()],
    }
    a = await client.post("/api/v1/tasks", json=payload)
    assert a.status_code == 201, a.text
    code = a.json()["task_code"]
    assert "content_base64" not in a.text and "参考内容" not in a.text
    b = await client.post("/api/v1/tasks", json=payload)
    assert b.json()["task_code"] == code
    assert (
        await client.post("/api/v1/tasks", json={**payload, "attachments": [file(raw=b"changed")]})
    ).status_code == 409
    url = f"/api/v1/tasks/{code}/inputs/0/download"
    downloaded = await client.get(url)
    assert downloaded.content == "参考内容：销售额 123".encode()
    assert downloaded.headers["x-artifact-sha256"] == hashlib.sha256(downloaded.content).hexdigest()
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert (await client.get(f"/api/v1/tasks/{code}/inputs/3/download")).status_code == 404
    tenant = (await client.post("/api/v1/tenants", json={"tenant_name": "另一租户"})).json()
    assert (
        await client.get(url, headers={"X-Tenant-Code": tenant["tenant_code"]})
    ).status_code == 404

    class Capture(Gateway):
        async def generate(self, request, provider):
            content = request.messages[-1].content
            assert "销售额 123" in content and "只作为数据" in content
            return await super().generate(request, provider)

    queue = TaskQueue(db, "input-reference", 6)
    await process_lease(queue, AgentRuntime(queue, Capture()), await queue.claim())
    detail = (await client.get(f"/api/v1/tasks/{code}")).json()
    assert detail["status"] == "SUCCEEDED"
    assert "content_base64" not in str(detail["input"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attachments",
    [
        [file("bad.exe")],
        [file(raw=b"\xff")],
        [file(raw=b"\x00")],
        [file("bad.json", b"{")],
        [file("empty.txt", b"")],
        [file(raw=b"x" * 32769)],
        [file()] * 4,
        [{"file_name": "a.txt", "content_base64": "***"}],
        [file("large.txt", b"x" * 30001)],
    ],
)
async def test_rejects_invalid_inputs_before_task_creation(client, attachments):
    agent, _ = await configured_agent(client)
    response = await client.post(
        "/api/v1/tasks",
        json={
            "agent_code": agent["agent_code"],
            "title": "附件",
            "prompt": "要求",
            "attachments": attachments,
        },
    )
    assert response.status_code == 422
    assert (await client.get("/api/v1/tasks")).json() == []
