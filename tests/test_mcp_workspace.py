import asyncio
import socket

import pytest
import pytest_asyncio
import uvicorn
from mcp.server.mcpserver import MCPServer
from test_platform_v1 import configured_agent, post

from app.core.config import Settings
from app.tools import mcp

pytest_plugins = ["test_platform_v1"]


@pytest_asyncio.fixture
async def mcp_endpoint(monkeypatch):
    server = MCPServer("local-acceptance")

    @server.tool()
    def word_count(text: str) -> str:
        return f"Words: {len(text.split())}"

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    application = server.streamable_http_app(stateless_http=True, json_response=True)
    runner = uvicorn.Server(uvicorn.Config(application, log_level="warning", access_log=False))
    task = asyncio.create_task(runner.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not runner.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        settings = Settings(_env_file=None, environment="local", mcp_allowed_hosts=["127.0.0.1"])
        monkeypatch.setattr(mcp, "get_settings", lambda: settings)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        runner.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


async def test_real_mcp_protocol_discovery_and_tool_execution(mcp_endpoint):
    catalog = await mcp.mcp_request(mcp_endpoint)
    assert catalog[0]["name"] == "word_count"
    assert catalog[0]["input_schema"]["properties"]["text"]["type"] == "string"
    result = await mcp.mcp_request(
        mcp_endpoint, tool_name="word_count", arguments={"text": "hello world"}
    )
    assert "Words: 2" in str(result)


async def test_mcp_configuration_detect_snapshot_and_credential_redaction(
    client, db, mcp_endpoint, monkeypatch
):
    from app.api import workspace

    old = workspace.get_settings()
    settings = Settings(
        _env_file=None,
        environment="local",
        mcp_allowed_hosts=["127.0.0.1"],
        credential_key_file=old.credential_key_file,
    )
    monkeypatch.setattr(workspace, "get_settings", lambda: settings)
    connection = await post(
        client,
        "/mcps",
        {"mcp_name": "本地 MCP", "endpoint": mcp_endpoint, "token": "isolated-mcp-secret"},
    )
    assert connection["has_token"] and "isolated-mcp-secret" not in str(connection)
    checked = await post(client, f"/mcps/{connection['mcp_code']}/check", {}, status=200)
    assert checked["checked_at"] and checked["tools"][0]["name"] == "word_count"
    agent, old = await configured_agent(client)
    version = await post(
        client,
        f"/agents/{agent['agent_code']}/versions",
        {
            "profile_code": old["profile_code"],
            "system_prompt": "MCP助手",
            "mcp_codes": [connection["mcp_code"]],
        },
    )
    snapshot = version["dependency_snapshot"]["mcps"][0]
    assert snapshot["tools"][0]["name"] == "word_count"
    assert "isolated-mcp-secret" not in str(version)
    from test_platform_v1 import Gateway, queued

    from app.repositories.task_queue import TaskQueue
    from app.runtime.agent import AgentRuntime
    from app.workers.agent_worker import process_lease

    task = await queued(client, agent)
    queue = TaskQueue(db, "mcp-runtime", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway(tool=True)), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["status"] == "SUCCEEDED"
    trace = (await client.get("/api/v1/runs/" + detail["runs"][0]["run_code"])).json()
    assert any("Words: 2" in str(m.get("content")) for m in trace["messages"])
    rotated = await client.patch(
        "/api/v1/mcps/" + connection["mcp_code"],
        json={"mcp_name": "本地 MCP", "endpoint": mcp_endpoint, "token": "rotated-mcp-secret"},
    )
    assert rotated.status_code == 200
    assert rotated.json()["checked_at"] is None and rotated.json()["tools"] == []
    detail = await client.get("/api/v1/mcps/" + connection["mcp_code"])
    assert detail.status_code == 200 and "rotated-mcp-secret" not in detail.text
    await post(
        client,
        f"/agents/{agent['agent_code']}/versions",
        {
            "profile_code": old["profile_code"],
            "system_prompt": "需重新检测",
            "mcp_codes": [connection["mcp_code"]],
        },
        status=409,
    )


def test_endpoint_and_schema_boundaries(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        mcp, "get_settings", lambda: Settings(_env_file=None, mcp_allowed_hosts=["safe.example"])
    )
    for url in [
        "https://evil.example/mcp",
        "http://safe.example/mcp",
        "https://user:secret@safe.example/mcp",
        "https://safe.example/mcp?token=secret",
    ]:
        with pytest.raises(HTTPException):
            mcp.validate_mcp_endpoint(url)
    assert mcp.validate_mcp_endpoint("https://safe.example/mcp") == "https://safe.example/mcp"
    with pytest.raises(ValueError, match="远程引用"):
        mcp.validate_schema({"$ref": "https://evil.example/schema"})
