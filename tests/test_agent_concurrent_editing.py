import asyncio

from test_platform_v1 import conditional, configured_agent, post, queued

pytest_plugins = ["test_platform_v1"]


async def read(client, code):
    response = await client.get(f"/api/v1/agents/{code}/configuration")
    assert response.status_code == 200, response.text
    value = response.json()
    assert response.headers["etag"] == value["etag"]
    return value


def body(current, *, name=None, prompt="本地输入", publish=False):
    agent = current["agent"]
    return {
        "agent": {
            "agent_name": name or agent["agent_name"],
            "agent_type": agent["agent_type"],
            "status": agent["status"],
        },
        "config": {
            "profile_code": current["versions"][0]["profile_code"],
            "system_prompt": prompt,
            "revision": current["draft"]["revision"],
        },
        "publish": publish,
    }


async def save(client, code, current, **kwargs):
    return await client.put(
        f"/api/v1/agents/{code}/configuration",
        json=body(current, **kwargs),
        headers={"If-Match": current["etag"]},
    )


async def test_snapshot_is_stable_and_preconditions_are_required(client):
    created = await post(client, "/agents", {"agent_name": "新建初始快照"})
    assert (await read(client, created["agent_code"]))["etag"] == created["configuration_etag"]
    agent, _ = await configured_agent(client)
    code = agent["agent_code"]
    current = await read(client, code)
    assert (await read(client, code))["etag"] == current["etag"]
    assert "runtime-test-secret" not in str(current) and "api_key_ciphertext" not in str(current)
    path = f"/api/v1/agents/{code}"
    response = await client.put(path + "/configuration", json=body(current))
    assert response.status_code == 428
    assert (
        await client.post(path + "/versions", json=body(current)["config"] | {})
    ).status_code == 422
    config = {k: v for k, v in body(current)["config"].items() if k != "revision"}
    assert (await client.post(path + "/versions", json=config)).status_code == 428
    assert (await client.patch(path, json=body(current)["agent"])).status_code == 428
    assert (
        await client.post(
            path + "/rollback", json={"version_code": current["versions"][0]["version_code"]}
        )
    ).status_code == 428
    assert (await read(client, code))["draft"]["revision"] == 0


async def test_stale_save_cannot_partially_overwrite_metadata_or_draft(client):
    agent, _ = await configured_agent(client)
    code = agent["agent_code"]
    old = await read(client, code)
    changed = await conditional(
        client,
        "PATCH",
        f"/api/v1/agents/{code}",
        json={**body(old)["agent"], "agent_name": "其他编辑者的名称"},
    )
    assert changed.status_code == 200
    rejected = await save(client, code, old, name="过期编辑者名称", prompt="不得写入")
    assert rejected.status_code == 412
    current = await read(client, code)
    assert current["agent"]["agent_name"] == "其他编辑者的名称"
    assert current["draft"]["revision"] == 0 and current["draft"]["config"] is None


async def test_concurrent_atomic_saves_have_one_winner_and_coherent_fields(client):
    agent, _ = await configured_agent(client)
    code = agent["agent_code"]
    current = await read(client, code)
    responses = await asyncio.gather(
        *(save(client, code, current, name=name, prompt=name) for name in ["编辑者A", "编辑者B"])
    )
    assert sorted(r.status_code for r in responses) == [200, 412]
    latest = await read(client, code)
    assert latest["draft"]["revision"] == 1
    assert latest["agent"]["agent_name"] == latest["draft"]["config"]["system_prompt"]


async def test_failed_publication_rolls_back_metadata_draft_and_version(client):
    agent, version = await configured_agent(client)
    code = agent["agent_code"]
    current = await read(client, code)
    assert (
        await save(client, code, current, name="已保存名称", prompt="已保存草稿")
    ).status_code == 200
    current = await read(client, code)
    await client.patch("/api/v1/model-profiles/" + version["profile_code"], json={"enabled": False})
    response = await save(
        client, code, current, name="不得部分写入", prompt="不得写入", publish=True
    )
    assert response.status_code == 409
    latest = await read(client, code)
    assert latest["etag"] == current["etag"]
    assert latest["agent"]["agent_name"] == "已保存名称"
    assert latest["draft"]["config"]["system_prompt"] == "已保存草稿"
    assert len(latest["versions"]) == 1


async def test_relation_edit_delete_and_add_require_current_collection(client):
    child, _ = await configured_agent(client, "子 Agent")
    master, _ = await configured_agent(client, "主 Agent")
    code = master["agent_code"]
    path = f"/api/v1/agents/{code}/relations"
    old = await read(client, code)
    assert (
        await client.post(path, json={"sub_agent_code": child["agent_code"]})
    ).status_code == 428
    relation = await post(
        client, f"/agents/{code}/relations", {"sub_agent_code": child["agent_code"]}
    )
    stale = await read(client, code)
    changed = await client.patch(
        path + "/" + relation["relation_code"],
        json={"timeout_seconds": 31},
        headers={"If-Match": stale["relation_etag"]},
    )
    assert changed.status_code == 200
    for method in ("PATCH", "DELETE"):
        response = await client.request(
            method,
            path + "/" + relation["relation_code"],
            headers={"If-Match": stale["relation_etag"]},
            **({"json": {"timeout_seconds": 7}} if method == "PATCH" else {}),
        )
        assert response.status_code == 412
    assert (await read(client, code))["relations"][0]["timeout_seconds"] == 31
    response = await client.post(
        path,
        json={"sub_agent_code": child["agent_code"]},
        headers={"If-Match": old["relation_etag"]},
    )
    assert response.status_code == 412


async def test_publish_detects_changed_child_version_and_rollback_detects_pointer_change(client):
    child, child_version = await configured_agent(client, "子版本")
    master, version = await configured_agent(client, "主版本")
    code = master["agent_code"]
    path = f"/api/v1/agents/{code}"
    await post(client, f"/agents/{code}/relations", {"sub_agent_code": child["agent_code"]})
    old = await read(client, code)
    await post(
        client,
        f"/agents/{child['agent_code']}/versions",
        {"profile_code": child_version["profile_code"], "system_prompt": "新的子版本"},
    )
    response = await client.post(
        path + "/versions",
        json={"profile_code": version["profile_code"], "system_prompt": "旧页面发布"},
        headers={"If-Match": old["etag"]},
    )
    assert response.status_code == 412
    current = await read(client, code)
    assert (await save(client, code, current, publish=True)).status_code == 200
    response = await client.post(
        path + "/rollback",
        json={"version_code": version["version_code"]},
        headers={"If-Match": current["etag"]},
    )
    assert response.status_code == 412


async def test_atomic_publish_keeps_pinned_task_and_published_history(client):
    agent, old_version = await configured_agent(client)
    code = agent["agent_code"]
    task = await queued(client, agent)
    current = await read(client, code)
    response = await save(client, code, current, name="新名称", prompt="新提示词", publish=True)
    assert response.status_code == 200, response.text
    current = await read(client, code)
    published = response.json()["version"]
    assert current["agent"]["current_version_code"] == published["version_code"]
    assert current["draft"]["config"]["system_prompt"] == published["system_prompt"] == "新提示词"
    pinned = (await client.get("/api/v1/tasks/" + task["task_code"])).json()
    assert pinned["agent_version_code"] == old_version["version_code"]
    historical = next(
        v for v in current["versions"] if v["version_code"] == old_version["version_code"]
    )
    assert historical == old_version


async def test_configuration_write_respects_tenant_and_disabled_scope(client):
    agent, _ = await configured_agent(client)
    code = agent["agent_code"]
    current = await read(client, code)
    other = await post(client, "/tenants", {"tenant_name": "隔离租户"})
    response = await client.put(
        f"/api/v1/agents/{code}/configuration",
        json=body(current),
        headers={"If-Match": current["etag"], "X-Tenant-Code": other["tenant_code"]},
    )
    assert response.status_code == 404
    tenant = current["agent"]["tenant_code"]
    assert (
        await client.patch(
            "/api/v1/tenants/" + tenant, json={"tenant_name": "停用诊断租户", "status": 0}
        )
    ).status_code == 200
    assert (await save(client, code, current)).status_code == 403
    assert (await read(client, code))["etag"] == current["etag"]


async def test_atomic_endpoint_checks_write_and_publish_permissions_independently(client):
    from app.api import auth

    agent, _ = await configured_agent(client)
    code = agent["agent_code"]
    current = await read(client, code)
    for username, permissions in [
        ("concurrentreader", ["agents.read"]),
        ("concurrenteditor", ["agents.read", "agents.manage"]),
    ]:
        role = await post(client, "/roles", {"role_name": username, "permissions": permissions})
        await post(
            client,
            "/users",
            {
                "username": username,
                "display_name": username,
                "password": "concurrency-test-password-2026",
            },
        )
        await post(
            client,
            "/members",
            {"username": username, "display_name": username, "role_code": role["role_code"]},
        )
    auth.get_settings().auth_mode = "password"
    for username in ("concurrentreader", "concurrenteditor"):
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "concurrency-test-password-2026"},
            )
        ).status_code == 200
        assert (await save(client, code, current, publish=True)).status_code == 403
        if username == "concurrentreader":
            assert (await save(client, code, current)).status_code == 403
        else:
            assert (await save(client, code, current)).status_code == 200
