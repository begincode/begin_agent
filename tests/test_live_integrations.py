import asyncio
import socket
from datetime import UTC, datetime

import httpx2
import pytest
import pytest_asyncio
import uvicorn
from test_platform_v1 import Gateway, configured_agent, post, queued

from app.api import workspace
from app.core.config import Settings
from app.repositories.task_queue import TaskQueue
from app.runtime.agent import AgentRuntime, safe_request_id
from app.tools import mcp
from app.tools.errors import McpRequestError, classify_mcp_error
from app.workers.agent_worker import process_lease
from scripts.mcp_acceptance_server import create_server
from scripts.verify_runtime_integrations import summarize

pytest_plugins = ["test_platform_v1"]
TOKEN = "acceptance-fixture-token-00000001"


@pytest_asyncio.fixture
async def authenticated_mcp(tmp_path, monkeypatch):
    control = tmp_path / "control.json"
    application, calls = create_server(TOKEN, control_file=control)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    runner = uvicorn.Server(uvicorn.Config(application, log_level="critical", access_log=False))
    task = asyncio.create_task(runner.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not runner.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        settings = Settings(_env_file=None, environment="local", mcp_allowed_hosts=["127.0.0.1"])
        monkeypatch.setattr(mcp, "get_settings", lambda: settings)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", control, calls
    finally:
        runner.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


async def test_authenticated_mcp_protocol_receipts_and_safe_failures(authenticated_mcp):
    endpoint, control, calls = authenticated_mcp
    with pytest.raises(McpRequestError) as exc:
        await mcp.mcp_request(endpoint, "incorrect-fixture-token")
    assert exc.value.code == "MCP_AUTH_FAILED"
    assert TOKEN not in str(exc.value) and "incorrect-fixture-token" not in str(exc.value)
    catalog = await mcp.mcp_request(endpoint, TOKEN)
    assert catalog[0]["name"] == "acceptance_add"
    result = await mcp.mcp_request(endpoint, TOKEN, "acceptance_add", {"a": 184, "b": 207})
    assert calls[-1]["result"]["sum"] == 391
    assert calls[-1]["result"]["receipt"] in str(result)
    with pytest.raises(McpRequestError) as exc:
        await mcp.mcp_request(endpoint, TOKEN, "acceptance_add", {"a": 1000001, "b": 0})
    assert exc.value.code == "MCP_TOOL_ERROR"
    control.write_text('{"http_status":503}')
    with pytest.raises(McpRequestError) as exc:
        await mcp.mcp_request(endpoint, TOKEN)
    assert exc.value.code == "MCP_UNAVAILABLE"


async def test_failed_discovery_revokes_success_and_recovers(
    client, authenticated_mcp, monkeypatch
):
    endpoint, control, _ = authenticated_mcp
    settings = workspace.get_settings().model_copy(update={"mcp_allowed_hosts": ["127.0.0.1"]})
    monkeypatch.setattr(workspace, "get_settings", lambda: settings)
    row = await post(
        client, "/mcps", {"mcp_name": "鉴权验收", "endpoint": endpoint, "token": TOKEN}
    )
    path = f"/mcps/{row['mcp_code']}/check"
    await post(client, path, {}, status=200)
    control.write_text('{"http_status":401}')
    failure = await client.post("/api/v1" + path, json={})
    assert failure.status_code == 502 and TOKEN not in failure.text
    row = (await client.get(f"/api/v1/mcps/{row['mcp_code']}")).json()
    assert row["checked_at"] is None and row["last_error"] == "MCP_AUTH_FAILED"
    assert row["tools"]  # Retained for reference, never treated as a successful check.
    agent, old = await configured_agent(client)
    await post(
        client,
        f"/agents/{agent['agent_code']}/versions",
        {
            "profile_code": old["profile_code"],
            "system_prompt": "blocked",
            "mcp_codes": [row["mcp_code"]],
        },
        status=409,
    )
    control.write_text('{"http_status":0}')
    row = await post(client, path, {}, status=200)
    assert row["checked_at"] and row["last_error"] is None


async def test_failure_from_old_configuration_cannot_replace_new_config(client, monkeypatch):
    from sqlalchemy import update

    from app.models.workspace import McpConnection
    from app.repositories import platform

    settings = workspace.get_settings().model_copy(update={"mcp_allowed_hosts": ["127.0.0.1"]})
    monkeypatch.setattr(workspace, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp, "get_settings", lambda: settings)
    row = await post(
        client, "/mcps", {"mcp_name": "竞态", "endpoint": "http://127.0.0.1:18033/mcp"}
    )

    async def changed(*args):
        async with platform.session_factory() as session:
            await session.execute(
                update(McpConnection)
                .where(McpConnection.mcp_code == row["mcp_code"])
                .values(endpoint="http://127.0.0.1:18034/mcp", checked_at=datetime.now(UTC))
            )
            await session.commit()
        raise McpRequestError("MCP_AUTH_FAILED")

    monkeypatch.setattr(workspace, "mcp_request", changed)
    response = await client.post(f"/api/v1/mcps/{row['mcp_code']}/check", json={})
    assert response.status_code == 409
    saved = (await client.get(f"/api/v1/mcps/{row['mcp_code']}")).json()
    assert saved["last_error"] is None and saved["checked_at"]


async def test_runtime_records_safe_model_ids_and_error_categories(client, db):
    from app.model_gateway.domain.chat import ChatMessage, ModelResponse
    from app.model_gateway.errors import ProviderRequestError

    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    queue = TaskQueue(db, "diagnostics", 6)

    class Realistic(Gateway):
        async def generate(self, request, provider):
            return ModelResponse(
                message=ChatMessage(role="assistant", content="OK"),
                finish_reason="stop",
                provider_request_id="request-123",
                usage={"total_tokens": 20},
            )

    await process_lease(queue, AgentRuntime(queue, Realistic()), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    trace = (await client.get("/api/v1/runs/" + detail["output"]["run_code"])).json()
    model = next(s for s in trace["steps"] if s["step_type"] == "MODEL_CALL")
    assert model["output"]["provider_request_id"] == "request-123"
    assert model["input"]["provider_code"] == "deepseek"

    class Denied(Gateway):
        async def generate(self, request, provider):
            raise ProviderRequestError("runtime-test-secret", 401, code="AUTH_FAILED")

    task = await queued(client, agent)
    await process_lease(queue, AgentRuntime(queue, Denied()), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    response = await client.get("/api/v1/runs/" + detail["runs"][0]["run_code"])
    model = next(s for s in response.json()["steps"] if s["step_type"] == "MODEL_CALL")
    assert (
        model["output"]["diagnostic_code"] == "AUTH_FAILED"
        and model["output"]["http_status"] == 401
    )
    assert "runtime-test-secret" not in response.text


def test_request_id_and_nested_error_classification_do_not_expose_remote_text():
    assert safe_request_id("valid-123:abc") == "valid-123:abc"
    assert safe_request_id("secret-in-id", ["secret"]) is None
    assert safe_request_id("https://service.test/?token=secret") is None
    assert safe_request_id("x" * 257) is None
    request = httpx2.Request("GET", "https://safe.test/mcp")
    response = httpx2.Response(429, request=request)
    error = httpx2.HTTPStatusError("never-return-this-secret", request=request, response=response)
    classified = classify_mcp_error(ExceptionGroup("private", [ExceptionGroup("private", [error])]))
    assert classified.code == "MCP_RATE_LIMITED" and classified.status_code == 429
    assert "never-return-this-secret" not in str(classified)
    assert classify_mcp_error(TimeoutError("private")).code == "MCP_TIMEOUT"


async def test_mcp_cancellation_propagates_without_becoming_protocol_error(monkeypatch):
    async def cancelled(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(mcp, "validate_mcp_endpoint", lambda value: value)
    monkeypatch.setattr(mcp, "_request", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await mcp.mcp_request("https://safe.test/mcp")


def test_runtime_report_does_not_confuse_model_text_with_actual_mcp_execution():
    detail = {
        "task_code": "tsk_test",
        "status": "SUCCEEDED",
        "output": {"text": "391 receipt_0123456789abcdef"},
        "runs": [],
        "artifacts": [],
    }
    report = summarize(detail, [], [])
    assert report["status"] == "FAILED"
    assert not report["checks"]["mcp_step_succeeded"]
    assert not report["checks"]["child_succeeded"]


async def test_runtime_mcp_auth_failure_has_trace_source_and_no_success_artifact(
    client, db, authenticated_mcp, monkeypatch
):
    from app.model_gateway.domain.chat import ChatMessage, FunctionCall, ModelResponse, ToolCall

    endpoint, control, _ = authenticated_mcp
    settings = workspace.get_settings().model_copy(update={"mcp_allowed_hosts": ["127.0.0.1"]})
    monkeypatch.setattr(workspace, "get_settings", lambda: settings)
    connector = await post(
        client, "/mcps", {"mcp_name": "回执服务", "endpoint": endpoint, "token": TOKEN}
    )
    await post(client, f"/mcps/{connector['mcp_code']}/check", {}, status=200)
    agent, old = await configured_agent(client)
    await post(
        client,
        f"/agents/{agent['agent_code']}/versions",
        {
            "profile_code": old["profile_code"],
            "system_prompt": "MCP",
            "mcp_codes": [connector["mcp_code"]],
        },
    )
    control.write_text('{"http_status":401}')

    class CallMcp(Gateway):
        async def generate(self, request, provider):
            return ModelResponse(
                message=ChatMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            function=FunctionCall(
                                name=request.tools[0].function.name, arguments='{"a":184,"b":207}'
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
                usage={"total_tokens": 20},
            )

    task = await queued(client, agent)
    queue = TaskQueue(db, "mcp-auth-trace", 6)
    await process_lease(queue, AgentRuntime(queue, CallMcp()), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert (
        detail["status"] == "FAILED"
        and detail["error_code"] == "TOOL_FAILED"
        and not detail["artifacts"]
    )
    response = await client.get("/api/v1/runs/" + detail["runs"][0]["run_code"])
    step = next(s for s in response.json()["steps"] if s["step_type"] == "TOOL_CALL")
    assert step["name"] == "回执服务 · acceptance_add"
    assert step["input"]["source"]["mcp_code"] == connector["mcp_code"]
    assert step["output"]["diagnostic_code"] == "MCP_AUTH_FAILED"
    assert TOKEN not in response.text
