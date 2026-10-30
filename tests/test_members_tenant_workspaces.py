from test_platform_v1 import post

from app.core.identity import LOCAL_TENANT, new_code
from app.models.platform import PlatformUser, TenantMember
from app.models.workspace import RoleDefinition

pytest_plugins = ["test_platform_v1"]


async def test_member_directory_pagination_states_details_and_tenant(client, db):
    disabled = await post(
        client, "/roles", {"role_name": "停用观察者", "permissions": ["tasks.read"], "status": 0}
    )
    codes = []
    async with db() as session:
        for i in range(61):
            user, member = new_code("usr"), new_code("mem")
            codes.append(member)
            session.add(
                PlatformUser(
                    user_code=user,
                    external_user_id=f"directory-fixture-{i}",
                    username=f"directory{i:02}",
                    display_name=f"目录成员{i:02}",
                    status=0 if i == 0 else 1,
                )
            )
            session.add(
                TenantMember(
                    tenant_code=LOCAL_TENANT,
                    member_code=member,
                    user_code=user,
                    role_code=disabled["role_code"] if i == 1 else "MEMBER",
                    status=0 if i == 2 else 1,
                )
            )
        session.add_all(
            [
                RoleDefinition(
                    tenant_code=LOCAL_TENANT,
                    role_code=new_code("rol"),
                    role_name=f"目录角色{i}",
                    permissions=["tasks.read"],
                    status=1,
                )
                for i in range(51)
            ]
        )
        await session.commit()
    first = (await client.get("/api/v1/members?limit=50")).json()
    second = (await client.get("/api/v1/members?limit=50&offset=50")).json()
    assert (
        len(first) == 50
        and len(second) == 12
        and len({r["member_code"] for r in first + second}) == 62
    )
    for i, state in enumerate(("ACCOUNT_DISABLED", "ROLE_DISABLED", "MEMBER_DISABLED")):
        row = (await client.get("/api/v1/members/" + codes[i])).json()
        assert row["access_state"] == state and row["effective_permissions"] == []
        assert "password_hash" not in row and "id" not in row
    assert len((await client.get("/api/v1/members?status=0")).json()) == 1
    found = (await client.get("/api/v1/members?search=目录成员60")).json()
    assert [r["member_code"] for r in found] == [codes[60]]
    assert (await client.get("/api/v1/members?role_code=" + disabled["role_code"])).json()[0][
        "member_code"
    ] == codes[1]
    options = (await client.get("/api/v1/members/roles")).json()["roles"]
    assert (
        len(options) == 54
        and not next(r for r in options if r["role_code"] == disabled["role_code"])["assignable"]
    )
    other = await post(client, "/tenants", {"tenant_name": "成员详情隔离"})
    assert (
        await client.get(
            "/api/v1/members/" + codes[60], headers={"X-Tenant-Code": other["tenant_code"]}
        )
    ).status_code == 404


async def test_member_manager_can_assign_subset_roles_without_role_edit_permission(client):
    from app.api import auth

    observer = await post(
        client, "/roles", {"role_name": "可授予观察者", "permissions": ["tasks.read"]}
    )
    manager = await post(
        client,
        "/roles",
        {"role_name": "成员委派者", "permissions": ["members.manage", "tasks.read"]},
    )
    for name, role in (("memberdelegate", manager["role_code"]), ("membertarget", "MEMBER")):
        await post(
            client,
            "/users",
            {"username": name, "display_name": name, "password": "members-test-password-2026"},
        )
        await post(client, "/members", {"username": name, "display_name": name, "role_code": role})
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "memberdelegate", "password": "members-test-password-2026"},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/roles")).status_code == 403
    roles = (await client.get("/api/v1/members/roles")).json()["roles"]
    assert next(r for r in roles if r["role_code"] == observer["role_code"])["assignable"]
    assert not next(r for r in roles if r["role_code"] == "TENANT_ADMIN")["assignable"]
    await post(
        client,
        "/members",
        {"username": "membertarget", "display_name": "不可提权", "role_code": "TENANT_ADMIN"},
        status=403,
    )
    saved = await post(
        client,
        "/members",
        {
            "username": "membertarget",
            "display_name": "不要更改账号姓名",
            "role_code": observer["role_code"],
        },
    )
    detail = (await client.get("/api/v1/members/" + saved["member_code"])).json()
    assert (
        detail["effective_permissions"] == ["tasks.read"]
        and detail["display_name"] == "membertarget"
    )


async def test_member_last_admin_protection_and_platform_permission_preview(client):
    me = (await client.get("/api/v1/me")).json()
    await post(
        client,
        "/members",
        {"user_code": me["user_code"], "display_name": "管理员", "role_code": "MEMBER"},
        status=409,
    )
    user = await post(
        client,
        "/users",
        {
            "username": "memberplatform",
            "display_name": "平台身份",
            "password": "members-test-password-2026",
            "platform_admin": True,
        },
    )
    member = await post(
        client,
        "/members",
        {
            "user_code": user["user_code"],
            "display_name": "平台身份",
            "role_code": "MEMBER",
            "status": 0,
        },
    )
    value = (await client.get("/api/v1/members/" + member["member_code"])).json()
    assert value["access_state"] == "ACTIVE" and value["permission_source"] == "平台管理员"
    assert "members.manage" in value["effective_permissions"]


async def test_tenant_manager_renames_only_current_tenant_without_state_privilege(client, db):
    from sqlalchemy import select

    from app.api import auth
    from app.models.platform import Tenant

    other = await post(client, "/tenants", {"tenant_name": "保持不变的另一个租户"})
    role = await post(
        client, "/roles", {"role_name": "仅管理租户名称", "permissions": ["tenant.manage"]}
    )
    await post(
        client,
        "/users",
        {
            "username": "tenantmanager",
            "display_name": "租户管理者",
            "password": "tenant-test-password-2026",
        },
    )
    await post(
        client,
        "/members",
        {"username": "tenantmanager", "display_name": "租户管理者", "role_code": role["role_code"]},
    )
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "tenantmanager", "password": "tenant-test-password-2026"},
        )
    ).status_code == 200
    value = (await client.get("/api/v1/tenant")).json()
    assert value["tenant"]["tenant_code"] == LOCAL_TENANT
    assert all(value[k] is None for k in ("members", "agents", "resources", "administrators"))
    renamed = await client.patch(
        "/api/v1/tenant?tenant_code=" + other["tenant_code"],
        json={"tenant_name": "  当前租户新名称  "},
    )
    assert (
        renamed.status_code == 200
        and renamed.json()["tenant_code"] == LOCAL_TENANT
        and renamed.json()["tenant_name"] == "当前租户新名称"
    )
    assert (
        await client.patch("/api/v1/tenant", json={"tenant_name": "不可停用", "status": 0})
    ).status_code == 422
    assert (
        await client.patch(
            "/api/v1/tenants/" + LOCAL_TENANT, json={"tenant_name": "越权", "status": 0}
        )
    ).status_code == 403
    assert (
        await client.get("/api/v1/tenant", headers={"X-Tenant-Code": other["tenant_code"]})
    ).status_code == 403
    async with db() as session:
        row = await session.scalar(select(Tenant).where(Tenant.tenant_code == other["tenant_code"]))
        assert row.tenant_name == "保持不变的另一个租户"


async def test_tenant_readonly_and_platform_admin_recovery(client):
    from app.api import accounts, auth

    tenant = await post(client, "/tenants", {"tenant_name": "可恢复的租户"})
    await post(
        client,
        "/users",
        {
            "username": "tenantplatform",
            "display_name": "平台恢复者",
            "password": "tenant-test-password-2026",
            "platform_admin": True,
        },
    )
    await post(
        client,
        "/users",
        {
            "username": "tenantordinary",
            "display_name": "租户管理员",
            "password": "tenant-test-password-2026",
        },
    )
    response = await client.post(
        "/api/v1/members",
        headers={"X-Tenant-Code": tenant["tenant_code"]},
        json={
            "username": "tenantordinary",
            "display_name": "租户管理员",
            "role_code": "TENANT_ADMIN",
        },
    )
    assert response.status_code == 201
    await client.patch(
        "/api/v1/tenants/" + tenant["tenant_code"],
        json={"tenant_name": tenant["tenant_name"], "status": 0},
    )
    auth.get_settings().auth_mode = "password"
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "tenantplatform", "password": "tenant-test-password-2026"},
        )
    ).status_code == 200
    admin_cookie = client.cookies.get(accounts.COOKIE)
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "tenantordinary", "password": "tenant-test-password-2026"},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/tenant")).json()["tenant"]["status"] == 0
    assert (await client.patch("/api/v1/tenant", json={"tenant_name": "不可写"})).status_code == 403
    restored = await client.patch(
        "/api/v1/tenants/" + tenant["tenant_code"],
        headers={"Cookie": accounts.COOKIE + "=" + admin_cookie},
        json={"tenant_name": "已恢复", "status": 1},
    )
    assert restored.status_code == 200, restored.text
    assert (await client.get("/api/v1/tenant")).json()["tenant"]["status"] == 1
    assert (
        await client.patch("/api/v1/tenant", json={"tenant_name": "恢复后可以改名"})
    ).status_code == 200


async def test_current_tenant_counts_and_resource_state_are_scoped(client):
    from test_platform_v1 import configured_agent

    _, version = await configured_agent(client)
    value = (await client.get("/api/v1/tenant")).json()
    assert value["members"] == 1 and value["administrators"] == 1
    assert (
        value["agents"]
        == value["published_agents"]
        == value["resources"]
        == value["available_resources"]
        == 1
    )
    await client.put(
        "/api/v1/resources/grant",
        json={"resource_kind": "MODEL", "resource_code": version["profile_code"], "enabled": False},
    )
    assert (await client.get("/api/v1/tenant")).json()["available_resources"] == 0
    other = await post(client, "/tenants", {"tenant_name": "空白租户概况"})
    other_stats = (
        await client.get("/api/v1/tenant", headers={"X-Tenant-Code": other["tenant_code"]})
    ).json()
    assert (
        other_stats["agents"] == 0 and other_stats["resources"] == 0 and other_stats["members"] == 1
    )
