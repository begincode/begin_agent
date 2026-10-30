import asyncio
from datetime import UTC, datetime

from test_platform_v1 import Gateway, configured_agent, post, queued

from app.core.identity import LOCAL_TENANT, new_code
from app.models.platform import ToolDefinition
from app.models.workspace import McpConnection, SkillDefinition
from app.repositories.task_queue import TaskQueue
from app.runtime.agent import AgentRuntime
from app.tools.builtins import CATALOG
from app.workers.agent_worker import process_lease

pytest_plugins = ["test_platform_v1"]


async def test_resource_overview_paging_states_and_tenant_isolation(client, db):
    await configured_agent(client)
    tools = [new_code("tol") for _ in range(61)]
    async with db() as session:
        session.add_all(
            [
                ToolDefinition(
                    tenant_code=LOCAL_TENANT,
                    tool_code=code,
                    tool_name=f"目录工具{i:02}",
                    status=0 if i == 0 else 1,
                    input_schema=CATALOG["utc_now"],
                    connection_config={"implementation": "utc_now"},
                )
                for i, code in enumerate(tools)
            ]
        )
        session.add(
            SkillDefinition(
                tenant_code=LOCAL_TENANT,
                skill_code=new_code("skl"),
                skill_name="停用技能",
                status=0,
                instructions="instructions-must-not-appear-in-catalog",
            )
        )
        for name, checked, items in (
            ("未检测", None, []),
            ("已检测无工具", datetime.now(UTC), []),
            ("可用MCP", datetime.now(UTC), [{"name": "read"}]),
        ):
            session.add(
                McpConnection(
                    tenant_code=LOCAL_TENANT,
                    mcp_code=new_code("mcp"),
                    mcp_name=name,
                    endpoint="http://127.0.0.1:1/private-endpoint",
                    token_ciphertext="private-credential-must-not-appear",
                    status=1,
                    checked_at=checked,
                    tools=items,
                )
            )
        await session.commit()
    await client.put(
        "/api/v1/resources/grant",
        json={"resource_kind": "TOOL", "resource_code": tools[1], "enabled": False},
    )
    overview = (await client.get("/api/v1/resources/overview")).json()
    groups = {g["resource_kind"]: g for g in overview["groups"]}
    assert overview["total"] == 66 and overview["available"] == 61
    assert (
        groups["TOOL"]["available"] == 59
        and groups["TOOL"]["inactive"] == 1
        and groups["TOOL"]["revoked"] == 1
    )
    assert groups["MCP"]["unchecked"] == 2 and groups["MCP"]["available"] == 1
    first = (await client.get("/api/v1/resources?limit=50")).json()
    second = (await client.get("/api/v1/resources?limit=50&offset=50")).json()
    assert len(first) == 50 and len(second) == 16
    assert len({r["resource_code"] for r in first + second}) == 66
    assert all("input_schema" not in r and "endpoint" not in r for r in first + second)
    assert "private-credential" not in str(first + second) and "instructions-must-not" not in str(
        first + second
    )
    revoked = (await client.get("/api/v1/resources?resource_kind=TOOL&availability=REVOKED")).json()
    assert [r["resource_code"] for r in revoked] == [tools[1]] and revoked[0]["enabled"] is False
    assert (await client.get("/api/v1/resources?search=目录工具60")).json()[0][
        "resource_code"
    ] == tools[60]
    assert (await client.get("/api/v1/resources?resource_kind=UNKNOWN")).status_code == 422
    other = await post(client, "/tenants", {"tenant_name": "资源概况隔离"})
    headers = {"X-Tenant-Code": other["tenant_code"]}
    assert (await client.get("/api/v1/resources", headers=headers)).json() == []
    assert (await client.get("/api/v1/resources/overview", headers=headers)).json()["total"] == 0
    assert (
        await client.put(
            "/api/v1/resources/grant",
            json={"resource_kind": "TOOL", "resource_code": tools[1], "enabled": True},
            headers=headers,
        )
    ).status_code == 404


async def test_resource_reader_cannot_grant_and_sees_no_member_agent_counts(client):
    from app.api import auth

    await configured_agent(client)
    role = await post(
        client, "/roles", {"role_name": "只读资源", "permissions": ["resources.read"]}
    )
    await post(
        client,
        "/users",
        {
            "username": "catalogreader",
            "display_name": "目录读者",
            "password": "catalogreader-password-2026",
        },
    )
    await post(
        client,
        "/members",
        {"username": "catalogreader", "display_name": "目录读者", "role_code": role["role_code"]},
    )
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "catalogreader", "password": "catalogreader-password-2026"},
        )
    ).status_code == 200
    overview = (await client.get("/api/v1/resources/overview")).json()
    assert overview["total"] == 1 and overview["members"] is None and overview["agents"] is None
    resource = (await client.get("/api/v1/resources")).json()[0]
    assert (
        await client.put(
            "/api/v1/resources/grant",
            json={
                "resource_kind": resource["resource_kind"],
                "resource_code": resource["resource_code"],
                "enabled": False,
            },
        )
    ).status_code == 403


async def test_relation_edit_snapshots_and_runtime_keep_original_child(client, db):
    child, child_version = await configured_agent(client, "协作子助手")
    master, master_version = await configured_agent(client, "协作主助手")
    code = master["agent_code"]
    relation = await post(
        client,
        f"/agents/{code}/relations",
        {"sub_agent_code": child["agent_code"], "call_mode": "SERIAL", "timeout_seconds": 37},
    )
    config = {"profile_code": master_version["profile_code"], "system_prompt": "协作主助手"}
    published = await post(client, f"/agents/{code}/versions", config)
    task = await queued(client, master)
    path = f"/api/v1/agents/{code}/relations/{relation['relation_code']}"
    changed = await client.patch(
        path, json={"call_mode": "ON_DEMAND", "timeout_seconds": 7, "enabled": False}
    )
    assert changed.status_code == 200 and not changed.json()["enabled"]
    partial = await client.patch(path, json={"timeout_seconds": 11})
    assert partial.status_code == 200 and partial.json()["enabled"] is False
    assert partial.json()["call_mode"] == "ON_DEMAND" and partial.json()["timeout_seconds"] == 11
    current = await post(client, f"/agents/{code}/versions", config)
    assert current["dependency_snapshot"]["children"] == []
    latest_child = await post(
        client,
        f"/agents/{child['agent_code']}/versions",
        {"profile_code": child_version["profile_code"], "system_prompt": "新子版本"},
    )
    listed = (await client.get(f"/api/v1/agents/{code}/relations")).json()
    assert (
        len(listed) == 1
        and not listed[0]["enabled"]
        and listed[0]["sub_agent_name"] == "协作子助手"
    )
    assert listed[0]["sub_agent_version_code"] == latest_child["version_code"]
    queue = TaskQueue(db, "edited-relation-snapshot", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway()), await queue.claim())
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert (
        detail["status"] == "SUCCEEDED"
        and detail["agent_version_code"] == published["version_code"]
    )
    assert (
        len(detail["runs"]) == 2
        and detail["runs"][1]["agent_version_code"] == child_version["version_code"]
    )
    old = (await client.get(f"/api/v1/agents/{code}/versions")).json()
    snapshot = next(v for v in old if v["version_code"] == published["version_code"])[
        "dependency_snapshot"
    ]["children"][0]
    assert snapshot["timeout_seconds"] == 37 and snapshot["call_mode"] == "SERIAL"
    assert (
        await client.patch(
            path, json={"call_mode": "PARALLEL", "timeout_seconds": 5, "enabled": True}
        )
    ).status_code == 200
    new = await post(client, f"/agents/{code}/versions", config)
    assert new["dependency_snapshot"]["children"][0]["timeout_seconds"] == 5
    assert new["dependency_snapshot"]["children"][0]["version_code"] == latest_child["version_code"]


async def test_relation_duplicates_reenable_cycles_and_concurrent_graph_changes(client):
    agents = [(await configured_agent(client, name))[0] for name in ("关系A", "关系B", "关系C")]
    a, b, c = [r["agent_code"] for r in agents]
    relation = await post(client, f"/agents/{a}/relations", {"sub_agent_code": b})
    path = f"/api/v1/agents/{a}/relations/{relation['relation_code']}"
    await post(client, f"/agents/{a}/relations", {"sub_agent_code": b}, status=409)
    await post(client, f"/agents/{a}/relations", {"sub_agent_code": a}, status=409)
    assert (await client.patch(path, json={"enabled": False})).status_code == 200
    reverse = await post(client, f"/agents/{b}/relations", {"sub_agent_code": a})
    assert (await client.patch(path, json={"enabled": True})).status_code == 409
    assert (
        await client.patch(
            path, json={"enabled": False, "call_mode": "SERIAL", "timeout_seconds": 9}
        )
    ).status_code == 200
    assert (
        await client.delete(f"/api/v1/agents/{b}/relations/{reverse['relation_code']}")
    ).status_code == 204
    assert (await client.patch(path, json={"enabled": True})).status_code == 200
    results = await asyncio.gather(
        client.post(f"/api/v1/agents/{b}/relations", json={"sub_agent_code": c}),
        client.post(f"/api/v1/agents/{c}/relations", json={"sub_agent_code": a}),
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    unpublished = await post(client, "/agents", {"agent_name": "尚未发布"})
    await post(
        client, f"/agents/{a}/relations", {"sub_agent_code": unpublished["agent_code"]}, status=409
    )
    other = await post(client, "/tenants", {"tenant_name": "协作隔离"})
    assert (
        await client.patch(
            path, json={"enabled": False}, headers={"X-Tenant-Code": other["tenant_code"]}
        )
    ).status_code == 404


async def test_collaboration_reader_cannot_edit(client):
    from app.api import auth

    child, _ = await configured_agent(client, "只读子助手")
    master, _ = await configured_agent(client, "只读主助手")
    relation = await post(
        client, f"/agents/{master['agent_code']}/relations", {"sub_agent_code": child["agent_code"]}
    )
    role = await post(client, "/roles", {"role_name": "协作只读", "permissions": ["agents.read"]})
    await post(
        client,
        "/users",
        {
            "username": "collabreader",
            "display_name": "协作读者",
            "password": "collabreader-password-2026",
        },
    )
    await post(
        client,
        "/members",
        {"username": "collabreader", "display_name": "协作读者", "role_code": role["role_code"]},
    )
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "collabreader", "password": "collabreader-password-2026"},
        )
    ).status_code == 200
    path = f"/api/v1/agents/{master['agent_code']}/relations"
    assert (await client.get(path)).json()[0]["sub_agent_name"] == "只读子助手"
    assert (
        await client.patch(path + "/" + relation["relation_code"], json={"enabled": False})
    ).status_code == 403
