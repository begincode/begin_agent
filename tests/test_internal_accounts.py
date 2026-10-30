import httpx
import pytest_asyncio
from sqlalchemy import select

from app.api import accounts, auth, chat, main, workspace
from app.core.config import Settings
from app.models.platform import PlatformUser
from app.models.workspace import AccountSession
from app.repositories import platform as repositories

pytest_plugins = ["test_platform_v1"]

PASSWORD = "local-test-password-2026"
ADMIN = {"username": "admin", "display_name": "管理员", "password": PASSWORD}


@pytest_asyncio.fixture
async def account_client(db, tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_mode="password",
        credential_key_file=str(tmp_path / "key"),
    )
    for module in (accounts, auth, chat, main, workspace):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    for module in (auth, chat, repositories):
        monkeypatch.setattr(module, "session_factory", db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.create_app(), client=("127.0.0.1", 1234)),
        base_url="http://localhost",
        headers={"Origin": "http://localhost"},
    ) as client:
        yield client


async def initialize(client):
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response


async def test_setup_creates_independent_admin_and_preserves_hashed_secrets(account_client, db):
    c = account_client
    assert (await c.get("/api/v1/auth/config")).json()["setup_required"]
    assert (await c.get("/api/v1/me")).status_code == 401
    response = await initialize(c)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert PASSWORD not in response.text and "password_hash" not in response.text
    me = (await c.get("/api/v1/me")).json()
    assert me["username"] == "admin" and me["platform_admin"] and len(me["tenants"]) == 1
    assert not (await c.get("/api/v1/auth/config")).json()["setup_required"]
    assert (await c.post("/api/v1/auth/setup", json=ADMIN)).status_code == 409
    async with db() as session:
        user = await session.scalar(select(PlatformUser).where(PlatformUser.username == "admin"))
        assert user.password_hash != PASSWORD
        assert accounts.verify_password(PASSWORD, user.password_hash)
        stored = await session.scalar(select(AccountSession))
        assert stored.token_hash != c.cookies[accounts.COOKIE]
        assert len(stored.token_hash) == 64


async def test_login_logout_and_password_change_revokes_other_sessions(account_client):
    c = account_client
    await initialize(c)
    first = c.cookies[accounts.COOKIE]
    await c.post("/api/v1/auth/logout")
    assert (await c.get("/api/v1/me")).status_code == 401
    assert (
        await c.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong-password-000"}
        )
    ).status_code == 401
    assert (
        await c.post("/api/v1/auth/login", json={"username": "ADMIN", "password": PASSWORD})
    ).status_code == 200
    second = c.cookies[accounts.COOKIE]
    assert second != first
    await c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    new_password = "changed-password-2026"
    assert (
        await c.post(
            "/api/v1/auth/password", json={"old_password": "bad", "password": new_password}
        )
    ).status_code == 403
    assert (
        await c.post(
            "/api/v1/auth/password", json={"old_password": PASSWORD, "password": new_password}
        )
    ).status_code == 200
    assert (
        await c.get("/api/v1/me", headers={"Authorization": "Bearer " + second})
    ).status_code == 401
    assert (await c.get("/api/v1/me")).status_code == 200
    assert (
        await c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    ).status_code == 401
    assert (
        await c.post("/api/v1/auth/login", json={"username": "admin", "password": new_password})
    ).status_code == 200
    assert (await c.post("/api/v1/auth/logout-all")).status_code == 200
    assert (await c.get("/api/v1/me")).status_code == 401


async def test_user_creation_membership_roles_reset_and_disable(account_client):
    c = account_client
    await initialize(c)
    me = (await c.get("/api/v1/me")).json()
    user = await c.post(
        "/api/v1/users", json={"username": "member", "display_name": "成员", "password": PASSWORD}
    )
    assert user.status_code == 201, user.text
    code = user.json()["user_code"]
    assert "password_hash" not in user.text and PASSWORD not in user.text
    assert (
        await c.post(
            "/api/v1/users",
            json={"username": "MEMBER", "display_name": "重复", "password": PASSWORD},
        )
    ).status_code == 409
    assert (
        await c.post(
            "/api/v1/members",
            json={"username": "member", "display_name": "成员", "role_code": "MEMBER"},
        )
    ).status_code == 201
    admin_cookie = c.cookies[accounts.COOKIE]
    assert (
        await c.post("/api/v1/auth/login", json={"username": "member", "password": PASSWORD})
    ).status_code == 200
    member_cookie = c.cookies[accounts.COOKIE]
    assert (await c.get("/api/v1/me")).json()["role"] == "MEMBER"
    assert (await c.get("/api/v1/users")).status_code == 403
    assert (
        await c.post(
            "/api/v1/users",
            json={
                "username": "attacker",
                "display_name": "越权",
                "password": PASSWORD,
                "platform_admin": True,
            },
        )
    ).status_code == 403
    assert (await c.post("/api/v1/agents", json={"agent_name": "越权"})).status_code == 403
    c.cookies.set(accounts.COOKIE, admin_cookie, domain="localhost.local", path="/")
    assert (
        await c.post(f"/api/v1/users/{code}/password", json={"password": "reset-password-2026"})
    ).status_code == 200
    assert (
        await c.get("/api/v1/me", headers={"Authorization": "Bearer " + member_cookie})
    ).status_code == 401
    assert (
        await c.post("/api/v1/auth/login", json={"username": "member", "password": PASSWORD})
    ).status_code == 401
    assert (
        await c.patch(f"/api/v1/users/{code}", json={"display_name": "成员", "status": 0})
    ).status_code == 200
    assert (
        await c.post(
            "/api/v1/auth/login", json={"username": "member", "password": "reset-password-2026"}
        )
    ).status_code == 401
    assert (
        await c.patch(
            "/api/v1/users/" + me["user_code"], json={"display_name": "管理员", "status": 0}
        )
    ).status_code == 409
    assert (
        await c.patch(
            "/api/v1/users/" + me["user_code"],
            json={"display_name": "管理员", "platform_admin": False},
        )
    ).status_code == 409


async def test_csrf_rate_limit_and_invalid_credentials(account_client):
    c = account_client
    assert (
        await c.post("/api/v1/auth/setup", json=ADMIN, headers={"Origin": "https://evil.example"})
    ).status_code == 403
    await initialize(c)
    assert (
        await c.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"})
    ).status_code == 403
    assert (
        await c.get("/api/v1/me", headers={"Authorization": "Bearer agt_sess_fake"})
    ).status_code == 401
    for _ in range(8):
        response = await c.post(
            "/api/v1/auth/login", json={"username": "unknown", "password": PASSWORD}
        )
        assert response.status_code == 401
    response = await c.post(
        "/api/v1/auth/login", json={"username": "unknown", "password": PASSWORD}
    )
    assert response.status_code == 429 and response.headers["retry-after"] == "900"
    response = await c.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "tiny!pass"}
    )
    assert response.status_code == 422 and "tiny!pass" not in response.text


def test_scrypt_uses_distinct_salts():
    one, two = accounts.password_hash(PASSWORD), accounts.password_hash(PASSWORD)
    assert one != two and accounts.verify_password(PASSWORD, one)
    assert not accounts.verify_password("wrong", one)
    assert not accounts.verify_password(PASSWORD, "broken-hash")


async def test_setup_preserves_existing_tenants_and_agents(account_client, db):
    from app.core.identity import new_code
    from app.models.platform import Agent, Tenant

    tenant, agent = new_code("ten"), new_code("agt")
    async with db() as session:
        session.add(Tenant(tenant_code=tenant, tenant_name="已有租户"))
        session.add(Agent(tenant_code=tenant, agent_code=agent, agent_name="已有 Agent"))
        await session.commit()
    await initialize(account_client)
    me = (await account_client.get("/api/v1/me")).json()
    assert me["tenant_code"] == tenant
    assert me["tenants"][0]["tenant_name"] == "已有租户"
    assert (await account_client.get("/api/v1/agents")).json()[0]["agent_code"] == agent


async def test_custom_role_cannot_read_tasks_through_overview(account_client):
    c = account_client
    await initialize(c)
    role = await c.post(
        "/api/v1/roles", json={"role_name": "只读 Agent", "permissions": ["agents.read"]}
    )
    assert role.status_code == 201
    await c.post(
        "/api/v1/users",
        json={"username": "reader", "display_name": "只读成员", "password": PASSWORD},
    )
    assert (
        await c.post(
            "/api/v1/members",
            json={
                "username": "reader",
                "display_name": "只读成员",
                "role_code": role.json()["role_code"],
            },
        )
    ).status_code == 201
    assert (
        await c.post("/api/v1/auth/login", json={"username": "reader", "password": PASSWORD})
    ).status_code == 200
    assert (await c.get("/api/v1/me")).json()["role_name"] == "只读 Agent"
    assert (await c.get("/api/v1/tasks")).status_code == 403
    overview = (await c.get("/api/v1/overview")).json()
    assert overview["tasks"] is None and overview["recent_tasks"] == []
    assert overview["activity"]["task_count"] is None and overview["activity"]["run_count"] is None
    assert overview["activity"]["daily"] == [] and overview["activity"]["model_usage"] == []
    assert (await c.get("/api/v1/agents")).status_code == 200
