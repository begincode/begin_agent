import json

import pytest
from test_platform_v1 import Gateway, configured_agent, post, queued

from app.model_gateway.domain.chat import ChatMessage, FunctionCall, ModelResponse, ToolCall
from app.repositories.task_queue import TaskQueue
from app.runtime.agent import AgentRuntime
from app.services import generated_files
from app.services.object_storage import StorageError
from app.workers.agent_worker import process_lease

pytest_plugins = ["test_platform_v1"]


class ExportGateway(Gateway):
    def __init__(self, fail=False, count=1):
        super().__init__()
        self.fail, self.count = fail, count

    async def generate(self, request, provider):
        if not any(m.role == "tool" for m in request.messages):
            return ModelResponse(
                message=ChatMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id=f"export{i}",
                            function=FunctionCall(
                                name=request.tools[0].function.name,
                                arguments=json.dumps(
                                    {
                                        "file_name": f"报告{i}.md",
                                        "content": "# 执行报告\n销售额 123 runtime-test-secret",
                                    }
                                ),
                            ),
                        )
                        for i in range(self.count)
                    ],
                ),
                finish_reason="tool_calls",
                usage={"total_tokens": 20},
            )
        if self.fail:
            raise ValueError("synthetic failed after staging")
        return await super().generate(request, provider)


async def prepare(client, db, monkeypatch, tmp_path, fail=False, count=1):
    from app.api import artifacts, auth

    settings = auth.get_settings().model_copy(
        update={"artifact_local_dir": str(tmp_path / "objects")}
    )
    monkeypatch.setattr(generated_files, "get_settings", lambda: settings)
    monkeypatch.setattr(artifacts, "get_settings", lambda: settings)
    tool = await post(
        client, "/tools", {"tool_name": "导出报告", "implementation": "export_text_file"}
    )
    agent, _ = await configured_agent(client, tools=[tool["tool_code"]])
    task = await queued(client, agent)
    queue = TaskQueue(db, "export-test", 12)
    return task, queue, ExportGateway(fail, count)


@pytest.mark.asyncio
async def test_generated_private_file_is_atomic_with_success_and_redacted(
    client, db, monkeypatch, tmp_path
):
    task, queue, gateway = await prepare(client, db, monkeypatch, tmp_path)
    await process_lease(queue, AgentRuntime(queue, gateway), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] == "SUCCEEDED" and "_generated_files" not in str(detail)
    file = next(a for a in detail["artifacts"] if a["artifact_type"] == "FILE")
    assert file["content"]["source"] == "GENERATED" and "storage" not in file["content"]
    result = await client.get(
        f"/api/v1/tasks/{task['task_code']}/artifacts/{file['artifact_code']}/download"
    )
    assert result.status_code == 200 and "销售额 123" in result.text
    assert "runtime-test-secret" not in result.text and "[REDACTED]" in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("fail,count", [(True, 1), (False, 4)])
async def test_failed_or_over_limit_execution_does_not_publish_staged_files(
    client, db, monkeypatch, tmp_path, fail, count
):
    task, queue, gateway = await prepare(client, db, monkeypatch, tmp_path, fail, count)
    await process_lease(queue, AgentRuntime(queue, gateway), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] != "SUCCEEDED"
    assert not any(a["artifact_type"] == "FILE" for a in detail["artifacts"])
    assert not list(tmp_path.rglob("art_*"))


@pytest.mark.asyncio
async def test_object_failure_compensates_and_never_marks_success(
    client, db, monkeypatch, tmp_path
):
    task, queue, gateway = await prepare(client, db, monkeypatch, tmp_path, count=2)
    from app.services.object_storage import LocalObjects

    original = LocalObjects.put
    calls = 0

    def fail_second(self, *args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StorageError("synthetic failure")
        return original(self, *args)

    monkeypatch.setattr(LocalObjects, "put", fail_second)
    await process_lease(queue, AgentRuntime(queue, gateway), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] != "SUCCEEDED"
    assert not any(a["artifact_type"] == "FILE" for a in detail["artifacts"])
    assert not list(tmp_path.rglob("art_*"))


@pytest.mark.asyncio
async def test_lost_commit_ack_retains_successful_objects(client, db, monkeypatch, tmp_path):
    task, queue, gateway = await prepare(client, db, monkeypatch, tmp_path)
    original = queue.finish

    async def lose_ack(*args, **kwargs):
        await original(*args, **kwargs)
        if kwargs.get("artifacts"):
            raise RuntimeError("commit acknowledgment lost")

    monkeypatch.setattr(queue, "finish", lose_ack)
    await process_lease(queue, AgentRuntime(queue, gateway), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] == "SUCCEEDED"
    file = next(a for a in detail["artifacts"] if a["artifact_type"] == "FILE")
    result = await client.get(
        f"/api/v1/tasks/{task['task_code']}/artifacts/{file['artifact_code']}/download"
    )
    assert result.status_code == 200 and list(tmp_path.rglob("art_*"))
