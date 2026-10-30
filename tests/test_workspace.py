import asyncio

from test_platform_v1 import Gateway, configured_agent, post, queued

from app.repositories.task_queue import TaskQueue
from app.runtime.agent import AgentRuntime
from app.workers.agent_worker import process_lease

pytest_plugins = ["test_platform_v1"]


async def finish(db, result="第一轮结果"):
    queue = TaskQueue(db, "workspace-test", 6)
    lease = await queue.claim()
    assert lease
    await queue.finish(lease, "SUCCEEDED", {"text": result})
    return lease


async def test_persistent_turns_concurrency_idempotency_and_original_task_key(client, db):
    agent, version = await configured_agent(client)
    original = {
        "agent_code": agent["agent_code"],
        "title": "多轮",
        "prompt": "第一轮",
        "idempotency_key": "task-key",
    }
    task = await post(client, "/tasks", original)
    code = task["task_code"]
    await post(client, f"/tasks/{code}/messages", {"prompt": "还不能追加"}, status=409)
    first = await finish(db)
    assert first.turn_no == 1
    value = {"prompt": "继续", "idempotency_key": "turn-2"}
    a, b = await asyncio.gather(
        post(client, f"/tasks/{code}/messages", value),
        post(client, f"/tasks/{code}/messages", value),
    )
    assert a["turn"]["turn_code"] == b["turn"]["turn_code"] and a["turn"]["turn_no"] == 2
    await post(
        client,
        f"/tasks/{code}/messages",
        {"prompt": "不同", "idempotency_key": "turn-2"},
        status=409,
    )
    assert (await post(client, "/tasks", original))["task_code"] == code
    second = await finish(db, "第二轮结果")
    assert second.turn_no == 2 and second.version_code == version["version_code"]
    detail = (await client.get("/api/v1/tasks/" + code)).json()
    assert [t["prompt"] for t in detail["turns"]] == ["第一轮", "继续"]
    assert [t["output"]["text"] for t in detail["turns"]] == ["第一轮结果", "第二轮结果"]
    assert all(t["status"] == "SUCCEEDED" for t in detail["turns"])


async def test_draft_revision_publish_rollback_and_existing_task_pin(client, db):
    agent, old = await configured_agent(client)
    code = agent["agent_code"]
    task = await queued(client, agent)
    config = {"profile_code": old["profile_code"], "system_prompt": "新的草稿", "revision": 0}
    assert (await client.get(f"/api/v1/agents/{code}/draft")).json()["revision"] == 0
    response = await client.put(f"/api/v1/agents/{code}/draft", json=config)
    assert response.status_code == 200 and response.json()["revision"] == 1
    again = await client.put(f"/api/v1/agents/{code}/draft", json={**config, "revision": 1})
    assert again.status_code == 200 and again.json()["revision"] == 2
    assert again.json()["updated_at"]
    assert (await client.put(f"/api/v1/agents/{code}/draft", json=config)).status_code == 409
    assert (await client.get("/api/v1/agents/" + code)).json()["current_version_code"] == old[
        "version_code"
    ]
    new = await post(
        client, f"/agents/{code}/versions", {k: v for k, v in config.items() if k != "revision"}
    )
    assert new["version_no"] == 2
    await post(
        client, f"/agents/{code}/rollback", {"version_code": old["version_code"]}, status=200
    )
    assert (await client.get("/api/v1/agents/" + code)).json()["current_version_code"] == old[
        "version_code"
    ]
    assert (await client.get("/api/v1/tasks/" + task["task_code"])).json()[
        "agent_version_code"
    ] == old["version_code"]
    other, _ = await configured_agent(client, "另一个")
    await post(
        client,
        f"/agents/{other['agent_code']}/rollback",
        {"version_code": old["version_code"]},
        status=409,
    )


async def test_draft_debug_does_not_publish_and_test_case_result(client, db):
    agent, version = await configured_agent(client)
    code = agent["agent_code"]
    config = {"profile_code": version["profile_code"], "system_prompt": "调试草稿", "revision": 0}
    await client.put(f"/api/v1/agents/{code}/draft", json=config)
    debug = await post(client, f"/agents/{code}/debug", {"prompt": "调试", "use_draft": True})
    assert debug["agent_version_code"] != version["version_code"]
    assert (await client.get("/api/v1/agents/" + code)).json()["current_version_code"] == version[
        "version_code"
    ]
    await finish(db)
    await post(
        client,
        f"/agents/{code}/rollback",
        {"version_code": debug["agent_version_code"]},
        status=409,
    )
    case = await post(
        client,
        f"/agents/{code}/test-cases",
        {"case_name": "包含文本", "prompt": "输入", "expected_contains": "expected"},
    )
    task = await post(client, f"/agents/{code}/test-cases/{case['case_code']}/run", None)
    await finish(db, "expected result")
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert detail["output"]["test_result"]["passed"] is True
    await post(
        client, f"/tasks/{task['task_code']}/messages", {"prompt": "不可改变用例"}, status=409
    )


async def test_history_contains_only_successful_turns_and_skill_snapshot(client, db):
    agent, old = await configured_agent(client)
    code = agent["agent_code"]
    skill = await post(
        client, "/skills", {"skill_name": "验收技能", "instructions": "SKILL_ORIGINAL_INSTRUCTIONS"}
    )
    version = await post(
        client,
        f"/agents/{code}/versions",
        {
            "profile_code": old["profile_code"],
            "system_prompt": "系统指令",
            "skill_codes": [skill["skill_code"]],
        },
    )
    await client.patch(
        "/api/v1/skills/" + skill["skill_code"],
        json={"skill_name": "已编辑技能", "instructions": "CHANGED_INSTRUCTIONS"},
    )
    task = await queued(client, agent)
    taskcode = task["task_code"]
    await finish(db, "成功上下文")
    await post(client, f"/tasks/{taskcode}/messages", {"prompt": "失败输入"})
    queue = TaskQueue(db, "history-test", 6)
    lease = await queue.claim()
    await queue.finish(lease, "FAILED", {"text": "失败输出"})
    await post(client, f"/tasks/{taskcode}/messages", {"prompt": "最后输入"})
    seen = []

    class RecordingGateway(Gateway):
        async def generate(self, request, provider):
            seen.extend(request.messages)
            return await super().generate(request, provider)

    lease = await queue.claim()
    await process_lease(queue, AgentRuntime(queue, RecordingGateway()), lease)
    text = " ".join(m.content or "" for m in seen)
    assert "成功上下文" in text and "最后输入" in text
    assert "失败输入" not in text and "失败输出" not in text
    assert "SKILL_ORIGINAL_INSTRUCTIONS" in text and "CHANGED_INSTRUCTIONS" not in text
    assert (await client.get("/api/v1/tasks/" + taskcode)).json()["status"] == "SUCCEEDED"
    assert (
        version["dependency_snapshot"]["skills"][0]["instructions"] == "SKILL_ORIGINAL_INSTRUCTIONS"
    )


async def test_resource_grants_stop_publish_and_runtime_and_audit(client, db):
    agent, version = await configured_agent(client)
    resources = (await client.get("/api/v1/resources")).json()
    model = next(r for r in resources if r["resource_kind"] == "MODEL")
    response = await client.put(
        "/api/v1/resources/grant",
        json={"resource_kind": "MODEL", "resource_code": model["resource_code"], "enabled": False},
    )
    assert response.status_code == 200
    await post(
        client,
        f"/agents/{agent['agent_code']}/versions",
        {"profile_code": version["profile_code"], "system_prompt": "不允许发布"},
        status=409,
    )
    task = await queued(client, agent)
    queue = TaskQueue(db, "grant-test", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway()), await queue.claim())
    assert (await client.get("/api/v1/tasks/" + task["task_code"])).json()["status"] == "FAILED"
    assert (await client.get("/api/v1/audit")).json()


async def test_cancelled_turn_can_continue_and_new_tables_are_tenant_scoped(client, db):
    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    code = task["task_code"]
    await post(client, f"/tasks/{code}/cancel", {}, status=200)
    detail = (await client.get("/api/v1/tasks/" + code)).json()
    assert detail["turns"][0]["status"] == "CANCELLED"
    await post(client, f"/tasks/{code}/messages", {"prompt": "继续"})
    assert (await client.get("/api/v1/tasks/" + code)).json()["turns"][1]["turn_no"] == 2
    tenant = await post(client, "/tenants", {"tenant_name": "隔离租户"})
    headers = {"X-Tenant-Code": tenant["tenant_code"]}
    assert (
        await client.get(f"/api/v1/agents/{agent['agent_code']}/draft", headers=headers)
    ).status_code == 404
    assert (await client.get("/api/v1/skills", headers=headers)).json() == []


async def test_task_manager_can_cancel_retry_but_cannot_create_tasks(client):
    from app.api import auth

    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    role = await post(
        client,
        "/roles",
        {"role_name": "任务管理", "permissions": ["tasks.read", "tasks.manage", "agents.read"]},
    )
    await post(
        client,
        "/users",
        {
            "username": "controller",
            "display_name": "任务管理者",
            "password": "controller-password-2026",
        },
    )
    await post(
        client,
        "/members",
        {"username": "controller", "display_name": "任务管理者", "role_code": role["role_code"]},
    )
    auth.get_settings().auth_mode = "password"
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "controller", "password": "controller-password-2026"},
    )
    assert login.status_code == 200, login.text
    assert (await client.get(f"/api/v1/tasks/{task['task_code']}/execution")).status_code == 403
    response = await client.post(f"/api/v1/tasks/{task['task_code']}/cancel", json={})
    assert response.status_code == 200, response.text
    response = await client.post(f"/api/v1/tasks/{task['task_code']}/retry", json={})
    assert response.status_code == 201, response.text
    assert (
        await client.post(
            "/api/v1/tasks",
            json={"agent_code": agent["agent_code"], "title": "不可新建", "prompt": "不允许"},
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/tasks/{task['task_code']}/messages", json={"prompt": "不可追加"}
        )
    ).status_code == 403


async def test_execution_summary_tree_usage_isolation_and_mine(client, db):
    from sqlalchemy import select

    from app.models.platform import AgentTask

    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    queue = TaskQueue(db, "execution-step-1", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway()), await queue.claim())
    result = await client.get(f"/api/v1/tasks/{task['task_code']}/execution")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["run_count"] == 1 and body["tokens"] == 20 and body["steps_count"] >= 2
    assert body["runs"][0]["agent_name"] == agent["agent_name"]
    assert body["runs"][0]["elapsed_ms"] >= 0
    assert "runtime-test-secret" not in result.text and "input" not in body["runs"][0]["steps"][0]
    async with db() as session:
        row = await session.scalar(
            select(AgentTask).where(AgentTask.task_code == task["task_code"])
        )
        row.created_by = "another-user"
        await session.commit()
    assert (await client.get("/api/v1/tasks?mine=true")).json() == []
    assert (await client.get("/api/v1/tasks")).json()
    tenant = await post(client, "/tenants", {"tenant_name": "执行隔离"})
    assert (
        await client.get(
            f"/api/v1/tasks/{task['task_code']}/execution",
            headers={"X-Tenant-Code": tenant["tenant_code"]},
        )
    ).status_code == 404


def test_usage_summary_does_not_fabricate_unknown_or_negative_values():
    from app.services.execution import token_count

    assert token_count(None) is None
    assert token_count({"total_tokens": -1}) is None
    assert token_count({"prompt_tokens": 3, "completion_tokens": 7}) == 10
    assert token_count({"total_tokens": 0}) == 0


async def test_case_latest_result_tracks_original_expectation_and_version(client, db):
    agent, version = await configured_agent(client)
    code = agent["agent_code"]
    case = await post(
        client,
        f"/agents/{code}/test-cases",
        {"case_name": "回归", "prompt": "输入", "expected_contains": "original"},
    )
    task = await post(
        client,
        f"/agents/{code}/test-cases/{case['case_code']}/run",
        {"version_code": version["version_code"]},
    )
    await finish(db, "original output")
    edited = await client.patch(
        f"/api/v1/agents/{code}/test-cases/{case['case_code']}",
        json={"case_name": "编辑过", "prompt": "新输入", "expected_contains": "new expected"},
    )
    assert edited.status_code == 200
    case = (await client.get(f"/api/v1/agents/{code}/test-cases")).json()[0]
    latest = case["latest_execution"]
    assert (
        latest["task_code"] == task["task_code"]
        and latest["version_code"] == version["version_code"]
    )
    assert (
        latest["test_result"]["passed"] and latest["test_result"]["expected_contains"] == "original"
    )


async def test_resource_pagination_groups_status_and_direct_details(client, db):
    from app.core.identity import LOCAL_TENANT, new_code
    from app.models.platform import ToolDefinition
    from app.tools.builtins import CATALOG

    async with db() as session:
        session.add_all(
            [
                ToolDefinition(
                    tenant_code=LOCAL_TENANT,
                    tool_code=new_code("tol"),
                    tool_name=f"分页工具{i:02}",
                    group_name="A" if i % 2 else "B",
                    status=1,
                    input_schema=CATALOG["utc_now"],
                    connection_config={"implementation": "utc_now"},
                )
                for i in range(61)
            ]
        )
        await session.commit()
    first = (await client.get("/api/v1/tools?limit=50")).json()
    second = (await client.get("/api/v1/tools?limit=50&offset=50")).json()
    assert len(first) == 50 and len(second) == 11
    assert not {r["tool_code"] for r in first} & {r["tool_code"] for r in second}
    tool = second[-1]
    assert (await client.get("/api/v1/tools/" + tool["tool_code"])).status_code == 200
    assert (await client.get("/api/v1/tools/groups")).json() == ["A", "B"]
    assert len((await client.get("/api/v1/tools?group_name=A&limit=100")).json()) == 30
    response = await client.patch(
        "/api/v1/tools/" + tool["tool_code"],
        json={
            "tool_name": tool["tool_name"],
            "group_name": "C",
            "implementation": "utc_now",
            "status": 0,
        },
    )
    assert response.status_code == 200 and response.json()["status"] == 0
    assert (await client.get("/api/v1/tools?status=0")).json()[0]["tool_code"] == tool["tool_code"]
    agent, version = await configured_agent(client)
    await post(
        client,
        f"/agents/{agent['agent_code']}/versions",
        {
            "profile_code": version["profile_code"],
            "system_prompt": "停用工具",
            "tool_codes": [tool["tool_code"]],
        },
        status=409,
    )
    tenant = await post(client, "/tenants", {"tenant_name": "资源隔离"})
    assert (
        await client.get(
            "/api/v1/tools/" + tool["tool_code"], headers={"X-Tenant-Code": tenant["tenant_code"]}
        )
    ).status_code == 404
    assert (
        await client.get(
            "/api/v1/model-profiles/" + version["profile_code"],
            headers={"X-Tenant-Code": tenant["tenant_code"]},
        )
    ).status_code == 404
    assert (
        "api_key"
        not in (await client.get("/api/v1/model-profiles/" + version["profile_code"])).text
    )


async def test_disabling_a_published_tool_prevents_execution(client, db, monkeypatch):
    from unittest.mock import Mock

    tool = await post(client, "/tools", {"tool_name": "可撤销工具", "implementation": "text_stats"})
    agent, _ = await configured_agent(client, tools=[tool["tool_code"]])
    task = await queued(client, agent)
    response = await client.patch(
        "/api/v1/tools/" + tool["tool_code"],
        json={"tool_name": "可撤销工具", "implementation": "text_stats", "status": 0},
    )
    assert response.status_code == 200
    execute = Mock()
    monkeypatch.setattr("app.runtime.agent.execute", execute)
    queue = TaskQueue(db, "revoked-tool", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway(tool=True)), await queue.claim())
    execute.assert_not_called()
    detail = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    trace = (await client.get("/api/v1/runs/" + detail["runs"][0]["run_code"])).json()
    assert any(s["step_type"] == "TOOL_CALL" and s["status"] == "FAILED" for s in trace["steps"])


async def test_activity_statistics_and_audit_actor_filters_are_real_and_scoped(client, db):
    tool = await post(client, "/tools", {"tool_name": "统计工具", "implementation": "text_stats"})
    agent, _ = await configured_agent(client, tools=[tool["tool_code"]])
    await queued(client, agent)
    queue = TaskQueue(db, "statistics", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway(tool=True)), await queue.claim())
    stats = (await client.get("/api/v1/overview?days=7")).json()["activity"]
    assert stats["task_count"] == 1 and stats["run_count"] == 1 and stats["model_calls"] == 2
    assert stats["tokens"] == 40 and stats["unreported_calls"] == 0 and stats["success_rate"] == 100
    assert len(stats["daily"]) == 7 and sum(d["total"] for d in stats["daily"]) == 1
    assert (
        stats["timezone"] == "Asia/Shanghai"
        and stats["model_usage"][0]["model_code"] == "deepseek-flash"
    )
    actors = (await client.get("/api/v1/audit/actors")).json()
    assert actors and "display_name" in actors[0]
    rows = (
        await client.get("/api/v1/audit", params={"actor_code": actors[0]["user_code"], "days": 7})
    ).json()
    assert rows and all(r["actor_code"] == actors[0]["user_code"] and r["actor_name"] for r in rows)
    assert all(r["action_name"] for r in rows)
    assert (await client.get("/api/v1/audit?actor_code=unknown")).json() == []
    tenant = await post(client, "/tenants", {"tenant_name": "统计隔离"})
    headers = {"X-Tenant-Code": tenant["tenant_code"]}
    other = (await client.get("/api/v1/overview?days=30", headers=headers)).json()["activity"]
    assert other["task_count"] == 0 and other["run_count"] == 0 and other["tokens"] is None
    assert not (await client.get("/api/v1/audit/actors", headers=headers)).json()


def test_business_date_window_and_invalid_usage_boundaries():
    from datetime import UTC, datetime

    from app.services.execution import token_count
    from app.services.statistics import date_window

    first, last, boundary = date_window(7, datetime(2026, 10, 29, 16, 30, tzinfo=UTC))
    assert str(last) == "2026-10-30" and str(first) == "2026-10-24"
    assert boundary == datetime(2026, 10, 23, 16, 0, tzinfo=UTC)
    assert token_count({"total_tokens": 10**400}) is None
    assert token_count({"total_tokens": float("nan")}) is None
