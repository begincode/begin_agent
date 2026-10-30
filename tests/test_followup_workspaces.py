import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from test_platform_v1 import Gateway, configured_agent, post, queued

from app.core.identity import LOCAL_TENANT, new_code
from app.models.platform import AgentRun, RunStep
from app.models.workspace import TaskTurn
from app.repositories.task_queue import TaskQueue
from app.runtime.agent import AgentRuntime
from app.workers.agent_worker import process_lease

pytest_plugins = ["test_platform_v1"]


async def test_run_summaries_filters_unknown_usage_and_tenant(client, db):
    agent, version = await configured_agent(client, "运行筛选助手")
    task = await post(
        client,
        "/tasks",
        {"agent_code": agent["agent_code"], "title": "定位月报", "prompt": "生成月报"},
    )
    queue = TaskQueue(db, "run-summary", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway()), await queue.claim())
    first = (await client.get("/api/v1/runs")).json()[0]
    async with db() as session:
        session.add(
            RunStep(
                tenant_code=LOCAL_TENANT,
                run_code=first["run_code"],
                step_code=new_code("stp"),
                step_no=100,
                step_type="MODEL_CALL",
                name="未上报用量",
                status="SUCCEEDED",
                input={"secret_input": "not-for-list"},
                output={"text": "private-output"},
            )
        )
        child = new_code("run")
        session.add(
            AgentRun(
                tenant_code=LOCAL_TENANT,
                run_code=child,
                task_code=task["task_code"],
                root_run_code=first["run_code"],
                parent_run_code=first["run_code"],
                agent_code=agent["agent_code"],
                agent_version_code=version["version_code"],
                lease_token="isolated-test",
                status="FAILED",
                run_depth=1,
                input={},
                created_at=datetime.now(UTC) - timedelta(days=40),
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
    rows = (
        await client.get(
            "/api/v1/runs",
            params={
                "search": "月报",
                "agent_code": agent["agent_code"],
                "task_code": task["task_code"],
            },
        )
    ).json()
    assert len(rows) == 2
    root = next(r for r in rows if r["run_code"] == first["run_code"])
    assert root["task_title"] == "定位月报" and root["agent_name"] == "运行筛选助手"
    assert root["tokens"] == 20 and root["model_calls"] == 2 and root["unreported_calls"] == 1
    assert root["elapsed_ms"] >= 0 and "input" not in root and "output" not in root
    assert "private-output" not in str(rows) and "lease_token" not in root
    recent = (await client.get("/api/v1/runs?days=30&root_only=true")).json()
    assert [r["run_code"] for r in recent] == [first["run_code"]]
    assert len((await client.get("/api/v1/runs?search=运行筛选助手")).json()) == 2
    assert (await client.get("/api/v1/runs?agent_code=missing")).json() == []
    pages = [
        (await client.get(f"/api/v1/runs?limit=1&offset={i}")).json()[0]["run_code"]
        for i in range(2)
    ]
    assert len(set(pages)) == 2
    detail = (await client.get("/api/v1/runs/" + first["run_code"])).json()
    assert detail["tokens"] == 20 and len(detail["steps"]) == root["steps_count"]
    other = await post(client, "/tenants", {"tenant_name": "运行隔离"})
    headers = {"X-Tenant-Code": other["tenant_code"]}
    assert (await client.get("/api/v1/runs", headers=headers)).json() == []
    assert (
        await client.get("/api/v1/runs/" + first["run_code"], headers=headers)
    ).status_code == 404


async def test_run_names_and_name_search_respect_separate_permissions(client, db):
    from app.api import auth

    agent, _ = await configured_agent(client, "受限助手名称")
    await queued(client, agent)
    queue = TaskQueue(db, "run-permissions", 6)
    await process_lease(queue, AgentRuntime(queue, Gateway()), await queue.claim())
    role = await post(client, "/roles", {"role_name": "只读运行", "permissions": ["runs.read"]})
    await post(
        client,
        "/users",
        {
            "username": "runreader",
            "display_name": "运行读者",
            "password": "runreader-password-2026",
        },
    )
    await post(
        client,
        "/members",
        {"username": "runreader", "display_name": "运行读者", "role_code": role["role_code"]},
    )
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "runreader", "password": "runreader-password-2026"},
        )
    ).status_code == 200
    row = (await client.get("/api/v1/runs")).json()[0]
    assert row["task_title"] is None and row["agent_name"] is None
    assert (await client.get("/api/v1/runs?search=受限助手名称")).json() == []
    assert (await client.get("/api/v1/runs?search=验收")).json() == []
    detail = (await client.get("/api/v1/runs/" + row["run_code"])).json()
    assert detail["task_title"] is None and detail["agent_name"] is None


async def finish_turn(db):
    queue = TaskQueue(db, "repeat-test", 6)
    lease = await queue.claim()
    assert lease
    await queue.finish(lease, "SUCCEEDED", {"text": "原成功结果"})


async def test_rerun_is_idempotent_pins_version_and_preserves_history(client, db):
    agent, version = await configured_agent(client)
    task = await queued(client, agent)
    code = task["task_code"]
    await finish_turn(db)
    await post(client, f"/tasks/{code}/messages", {"prompt": "最近一轮要求"})
    await finish_turn(db)
    before = (await client.get(f"/api/v1/tasks/{code}")).json()
    await post(
        client,
        f"/agents/{agent['agent_code']}/versions",
        {"profile_code": version["profile_code"], "system_prompt": "最新发布配置"},
    )
    await post(
        client,
        f"/tasks/{code}/rerun",
        {"source_turn_code": before["turns"][0]["turn_code"], "idempotency_key": "stale"},
        status=409,
    )
    body = {"source_turn_code": before["turns"][-1]["turn_code"], "idempotency_key": "repeat-last"}
    a, b = await asyncio.gather(
        post(client, f"/tasks/{code}/rerun", body), post(client, f"/tasks/{code}/rerun", body)
    )
    assert a["turn"]["turn_code"] == b["turn"]["turn_code"] and a["turn"]["turn_no"] == 3
    assert a["turn"]["prompt"] == "最近一轮要求"
    assert a["task"]["agent_version_code"] == version["version_code"]
    after = (await client.get(f"/api/v1/tasks/{code}")).json()
    assert [t["output"] for t in after["turns"][:2]] == [t["output"] for t in before["turns"]]
    await post(
        client, f"/tasks/{code}/rerun", {**body, "idempotency_key": "new-active"}, status=409
    )


async def test_rerun_rejects_disabled_agents_round_limit_and_test_tasks(client, db):
    agent, version = await configured_agent(client)
    task = await queued(client, agent)
    code = task["task_code"]
    await finish_turn(db)
    source = (await client.get(f"/api/v1/tasks/{code}")).json()["turns"][0]
    body = {"source_turn_code": source["turn_code"], "idempotency_key": "blocked"}
    await client.patch(
        "/api/v1/agents/" + agent["agent_code"], json={"agent_name": "停用", "status": 0}
    )
    await post(client, f"/tasks/{code}/rerun", body, status=409)
    await client.patch(
        "/api/v1/agents/" + agent["agent_code"], json={"agent_name": "恢复", "status": 1}
    )
    async with db() as session:
        turn = await session.scalar(
            select(TaskTurn).where(TaskTurn.turn_code == source["turn_code"])
        )
        turn.turn_no = 100
        await session.commit()
    await post(client, f"/tasks/{code}/rerun", body, status=409)
    case = await post(
        client,
        f"/agents/{agent['agent_code']}/test-cases",
        {"case_name": "固定用例", "prompt": "输入", "expected_contains": "结果"},
    )
    case_task = await post(
        client,
        f"/agents/{agent['agent_code']}/test-cases/{case['case_code']}/run",
        {"version_code": version["version_code"]},
    )
    await finish_turn(db)
    case_turn = (await client.get("/api/v1/tasks/" + case_task["task_code"])).json()["turns"][0]
    await post(
        client,
        f"/tasks/{case_task['task_code']}/rerun",
        {"source_turn_code": case_turn["turn_code"], "idempotency_key": "case"},
        status=409,
    )
    other = await post(client, "/tenants", {"tenant_name": "重新执行隔离"})
    assert (
        await client.post(
            f"/api/v1/tasks/{code}/rerun",
            json=body,
            headers={"X-Tenant-Code": other["tenant_code"]},
        )
    ).status_code == 404


async def test_rerun_enforces_ownership_and_allows_task_manager(client, db):
    from app.api import auth

    agent, _ = await configured_agent(client)
    task = await queued(client, agent)
    await finish_turn(db)
    code = task["task_code"]
    source = (await client.get(f"/api/v1/tasks/{code}")).json()["turns"][0]
    body = {"source_turn_code": source["turn_code"], "idempotency_key": "permissions"}
    manager = await post(
        client, "/roles", {"role_name": "仅管理任务", "permissions": ["tasks.read", "tasks.manage"]}
    )
    reader = await post(
        client, "/roles", {"role_name": "仅查看任务", "permissions": ["tasks.read"]}
    )
    for name, role in (
        ("repeatmember", "MEMBER"),
        ("repeatreader", reader["role_code"]),
        ("repeatmanager", manager["role_code"]),
    ):
        await post(
            client,
            "/users",
            {"username": name, "display_name": name, "password": "repeat-password-2026"},
        )
        await post(client, "/members", {"username": name, "display_name": name, "role_code": role})
    auth.get_settings().auth_mode = "password"
    for name in ("repeatmember", "repeatreader", "repeatmanager"):
        assert (
            await client.post(
                "/api/v1/auth/login", json={"username": name, "password": "repeat-password-2026"}
            )
        ).status_code == 200
        await post(
            client, f"/tasks/{code}/rerun", body, status=201 if name == "repeatmanager" else 403
        )
