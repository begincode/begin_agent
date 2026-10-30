from sqlalchemy import select
from test_platform_v1 import post

from app.core.identity import new_code
from app.models.platform import PlatformUser
from app.models.workspace import AccountSession

pytest_plugins = ["test_platform_v1"]


async def test_platform_user_directory_paging_filters_literal_search_and_detail(client, db):
    codes = []
    async with db() as session:
        for i in range(111):
            code = new_code("usr")
            codes.append(code)
            session.add(
                PlatformUser(
                    user_code=code,
                    external_user_id=f"directory-{i}",
                    username=f"pageuser{i:03}",
                    display_name="中文%Case" if i == 0 else f"目录账号{i}",
                    password_hash="not-a-real-credential",
                    status=0 if i % 3 == 0 else 1,
                    platform_admin=i % 5 == 0,
                )
            )
        await session.commit()
    pages = [
        (await client.get(f"/api/v1/users?limit=50&offset={offset}")).json()
        for offset in (0, 50, 100)
    ]
    assert [len(p) for p in pages] == [50, 50, 12]
    assert len({r["user_code"] for page in pages for r in page}) == 112
    assert (await client.get("/api/v1/users?search=pageuser110")).json()[0]["user_code"] == codes[
        -1
    ]
    assert (await client.get("/api/v1/users?search=case")).json()[0]["user_code"] == codes[0]
    assert len((await client.get("/api/v1/users?search=%25")).json()) == 1
    filtered = (await client.get("/api/v1/users?status=0&platform_admin=true")).json()
    assert len(filtered) == 8 and all(r["status"] == 0 and r["platform_admin"] for r in filtered)
    detail = (await client.get("/api/v1/users/" + codes[-1])).json()
    assert detail["account_kind"] == "PASSWORD" and detail["username"] == "pageuser110"
    assert not {"id", "password_hash"}.intersection(detail)
    assert all("password_hash" not in r for page in pages for r in page)
    assert (await client.get("/api/v1/users/does-not-exist")).status_code == 404
    assert (await client.get("/api/v1/users?limit=101")).status_code == 422
    assert (await client.get("/api/v1/users?status=2")).status_code == 422


async def test_user_directory_and_detail_require_platform_identity(client):
    from app.api import auth

    user = await post(
        client,
        "/users",
        {
            "username": "ordinaryuser",
            "display_name": "普通账号",
            "password": "directory-test-password-2026",
        },
    )
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "ordinaryuser", "password": "directory-test-password-2026"},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/users")).status_code == 403
    assert (await client.get("/api/v1/users/" + user["user_code"])).status_code == 403
    assert (await client.get("/api/v1/users/nonexistent")).status_code == 403


async def test_user_edit_disable_and_reset_revoke_sessions_with_last_admin_guard(client, db):
    from app.api import accounts, auth

    password = "directory-test-password-2026"
    admin = await post(
        client,
        "/users",
        {
            "username": "directoryadmin",
            "display_name": "管理员",
            "password": password,
            "platform_admin": True,
        },
    )
    target = await post(
        client,
        "/users",
        {"username": "directorytarget", "display_name": "目标", "password": password},
    )
    auth.get_settings().auth_mode = "password"
    await client.post(
        "/api/v1/auth/login", json={"username": "directorytarget", "password": password}
    )
    target_cookie = client.cookies.get(accounts.COOKIE)
    await client.post(
        "/api/v1/auth/login", json={"username": "directoryadmin", "password": password}
    )
    admin_cookie = client.cookies.get(accounts.COOKIE)
    assert (
        await client.patch(
            "/api/v1/users/" + admin["user_code"], json={"display_name": "管理员", "status": 0}
        )
    ).status_code == 409
    assert (
        await client.patch(
            "/api/v1/users/" + admin["user_code"],
            json={"display_name": "管理员", "status": 1, "platform_admin": False},
        )
    ).status_code == 409
    code = target["user_code"]
    assert (
        await client.patch("/api/v1/users/" + code, json={"display_name": "停用目标", "status": 0})
    ).status_code == 200
    assert (
        await client.get("/api/v1/me", headers={"Cookie": accounts.COOKIE + "=" + target_cookie})
    ).status_code == 401
    assert (
        await client.patch(
            "/api/v1/users/" + code,
            json={"display_name": "恢复目标", "status": 1},
            headers={"Cookie": accounts.COOKIE + "=" + admin_cookie},
        )
    ).status_code == 200
    await client.post(
        "/api/v1/auth/login", json={"username": "directorytarget", "password": password}
    )
    target_cookie = client.cookies.get(accounts.COOKIE)
    response = await client.post(
        "/api/v1/users/" + code + "/password",
        json={"password": "reset-directory-password-2026"},
        headers={"Cookie": accounts.COOKIE + "=" + admin_cookie},
    )
    assert response.status_code == 200
    assert (
        await client.get("/api/v1/me", headers={"Cookie": accounts.COOKIE + "=" + target_cookie})
    ).status_code == 401
    async with db() as session:
        assert (
            await session.scalars(select(AccountSession).where(AccountSession.user_code == code))
        ).all() == []


async def test_role_matrix_paging_counts_literal_search_and_cross_tenant(client, db):
    from app.core.identity import LOCAL_TENANT
    from app.models.platform import TenantMember
    from app.models.workspace import RoleDefinition

    target = await post(
        client, "/roles", {"role_name": "百分%Observer", "permissions": ["tasks.read"]}
    )
    async with db() as session:
        for i in range(58):
            session.add(
                RoleDefinition(
                    tenant_code=LOCAL_TENANT,
                    role_code=new_code("rol"),
                    role_name=f"矩阵角色{i}",
                    permissions=[],
                    status=1,
                )
            )
        for i in range(3):
            user = new_code("usr")
            session.add(
                PlatformUser(
                    user_code=user,
                    external_user_id=user,
                    display_name=user,
                    status=0 if i == 2 else 1,
                )
            )
            session.add(
                TenantMember(
                    tenant_code=LOCAL_TENANT,
                    member_code=new_code("mem"),
                    user_code=user,
                    role_code=target["role_code"],
                    status=0 if i == 1 else 1,
                )
            )
        await session.commit()
    pages = [
        (await client.get(f"/api/v1/roles?limit=50&offset={offset}")).json()["roles"]
        for offset in (0, 50)
    ]
    assert [len(p) for p in pages] == [50, 11]
    assert len({r["role_code"] for page in pages for r in page}) == 61
    assert all(not r["editable"] for r in pages[0][:2])
    found = (await client.get("/api/v1/roles?search=%25")).json()["roles"]
    assert (
        len(found) == 1 and found[0]["member_count"] == 3 and found[0]["active_member_count"] == 1
    )
    assert (await client.get("/api/v1/roles?search=observer")).json()["roles"][0][
        "role_code"
    ] == target["role_code"]
    assert (await client.get("/api/v1/roles?search=MEMBER")).json()["roles"][0]["builtin"]
    assert (
        await client.patch(
            "/api/v1/roles/" + target["role_code"],
            json={"role_name": target["role_name"], "permissions": ["tasks.read"], "status": 0},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/roles?status=0")).json()["roles"][0]["role_code"] == target[
        "role_code"
    ]
    other = await post(client, "/tenants", {"tenant_name": "角色矩阵隔离租户"})
    isolated = (
        await client.get("/api/v1/roles", headers={"X-Tenant-Code": other["tenant_code"]})
    ).json()["roles"]
    assert len(isolated) == 2 and all(r["role_code"] != target["role_code"] for r in isolated)
    assert (
        await client.patch(
            "/api/v1/roles/" + target["role_code"],
            headers={"X-Tenant-Code": other["tenant_code"]},
            json={"role_name": "越界", "permissions": []},
        )
    ).status_code == 404
    assert (await client.get("/api/v1/roles?limit=101")).status_code == 422


async def test_role_manager_cannot_overwrite_privileged_role_or_disable_self(client):
    from app.api import auth

    powerful = await post(
        client,
        "/roles",
        {"role_name": "更高权限", "permissions": ["roles.manage", "agents.manage"]},
    )
    manager = await post(
        client,
        "/roles",
        {"role_name": "受限角色管理", "permissions": ["roles.manage", "tasks.read"]},
    )
    user = await post(
        client,
        "/users",
        {
            "username": "rolemanager",
            "display_name": "角色管理者",
            "password": "role-directory-password-2026",
        },
    )
    await post(
        client,
        "/members",
        {
            "user_code": user["user_code"],
            "display_name": "角色管理者",
            "role_code": manager["role_code"],
        },
    )
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "rolemanager", "password": "role-directory-password-2026"},
        )
    ).status_code == 200
    values = (await client.get("/api/v1/roles")).json()["roles"]
    assert not next(r for r in values if r["role_code"] == powerful["role_code"])["editable"]
    assert all(r["member_count"] is None for r in values)
    assert (
        await client.patch(
            "/api/v1/roles/" + powerful["role_code"],
            json={"role_name": "间接撤权", "permissions": []},
        )
    ).status_code == 403
    assert (
        await client.patch(
            "/api/v1/roles/" + manager["role_code"],
            json={"role_name": "自身", "permissions": ["roles.manage"], "status": 0},
        )
    ).status_code == 409
    assert (
        await client.patch(
            "/api/v1/roles/" + manager["role_code"],
            json={"role_name": "仅查看任务", "permissions": ["tasks.read"]},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/me")).json()["permissions"] == ["tasks.read"]
    assert (await client.get("/api/v1/roles")).status_code == 403


async def test_role_changes_update_member_access_without_rewriting_history(client):
    from test_platform_v1 import configured_agent

    agent, version = await configured_agent(client)
    role = await post(client, "/roles", {"role_name": "权限联动", "permissions": ["tasks.read"]})
    user = await post(
        client,
        "/users",
        {
            "username": "rolelinked",
            "display_name": "权限联动成员",
            "password": "role-directory-password-2026",
        },
    )
    member = await post(
        client,
        "/members",
        {
            "user_code": user["user_code"],
            "display_name": "权限联动成员",
            "role_code": role["role_code"],
        },
    )
    assert (
        await client.patch(
            "/api/v1/roles/" + role["role_code"],
            json={"role_name": "权限联动", "permissions": ["tasks.read", "agents.read"]},
        )
    ).status_code == 200
    detail = (await client.get("/api/v1/members/" + member["member_code"])).json()
    assert detail["effective_permissions"] == ["agents.read", "tasks.read"]
    assert (
        await client.patch(
            "/api/v1/roles/" + role["role_code"],
            json={"role_name": "权限联动", "permissions": ["tasks.read"], "status": 0},
        )
    ).status_code == 200
    detail = (await client.get("/api/v1/members/" + member["member_code"])).json()
    assert detail["access_state"] == "ROLE_DISABLED" and detail["effective_permissions"] == []
    assert (await client.get("/api/v1/agents/" + agent["agent_code"] + "/versions")).json()[
        0
    ] == version
