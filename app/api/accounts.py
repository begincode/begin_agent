"""Built-in accounts: salted scrypt, opaque server sessions and DB-backed throttling."""

import asyncio
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr
from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.identity import new_code
from app.domain.platform import Input
from app.models.platform import PlatformUser, Tenant, TenantMember
from app.models.workspace import AccountSession, LoginBucket
from app.repositories.platform import database_session, public

accounts_router = APIRouter(prefix="/api/v1/auth", tags=["accounts"])
COOKIE = "begin_agent_session"
crypto_slots = asyncio.Semaphore(4)


class LoginInput(Input):
    username: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,31}$")
    password: SecretStr = Field(min_length=12, max_length=128)


class SetupInput(LoginInput):
    display_name: str = Field(min_length=1, max_length=128)


class CreateUserInput(SetupInput):
    platform_admin: bool = False


class ResetPasswordInput(Input):
    password: SecretStr = Field(min_length=12, max_length=128)


class PasswordInput(Input):
    old_password: SecretStr = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=12, max_length=128)


def password_hash(password, salt=None):
    salt = salt or os.urandom(16)
    derived = Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode())
    return "scrypt:" + salt.hex() + ":" + derived.hex()


def verify_password(password, encoded):
    try:
        _, salt, expected = encoded.split(":")
        if len(salt) != 32 or len(expected) != 64:
            return False
        actual = password_hash(password, bytes.fromhex(salt)).split(":")[-1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


DUMMY_HASH = password_hash("not-a-valid-account-password")


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def same_origin(request):
    origin = request.headers.get("origin")
    url = urlsplit(str(request.base_url))
    if origin != f"{url.scheme}://{url.netloc}":
        # Command line setup is permitted only over a real loopback connection.
        if origin is None and request.client and request.client.host in {"127.0.0.1", "::1"}:
            return
        raise HTTPException(403, "请求来源校验失败")


def credential(request):
    authorization = request.headers.get("authorization")
    if authorization is not None:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "登录凭证无效")
        return authorization[7:]
    return request.cookies.get(COOKIE, "")


def set_cookie(response, value, request, age=604800):
    # Cookies stay Secure on HTTPS; loopback HTTP is supported for local development.
    secure = request.url.scheme == "https" or get_settings().environment not in {"local", "test"}
    response.set_cookie(
        COOKIE, value, max_age=age, httponly=True, secure=secure, samesite="lax", path="/"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def setup_required(session):
    return not await session.scalar(
        select(PlatformUser.user_code)
        .where(
            PlatformUser.platform_admin.is_(True),
            PlatformUser.password_hash.is_not(None),
        )
        .limit(1)
    )


async def new_session(session, user):
    token = "agt_sess_" + secrets.token_urlsafe(32)
    session.add(
        AccountSession(
            token_hash=digest(token),
            user_code=user.user_code,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    await session.execute(delete(AccountSession).where(AccountSession.expires_at <= func.now()))
    return token


async def authenticate(request, factory):
    if "authorization" not in request.headers and request.method not in {"GET", "HEAD", "OPTIONS"}:
        same_origin(request)
    token = credential(request)
    if not token.startswith("agt_sess_") or len(token) != 52:
        raise HTTPException(401, "请登录账号")
    async with factory() as session:
        row = await session.scalar(
            select(PlatformUser)
            .join(AccountSession, AccountSession.user_code == PlatformUser.user_code)
            .where(
                AccountSession.token_hash == digest(token),
                AccountSession.expires_at > func.now(),
                PlatformUser.status == 1,
            )
        )
        if row is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        return row.external_user_id


@accounts_router.post("/setup", status_code=201)
async def setup(data: SetupInput, request: Request, session=Depends(database_session)):
    same_origin(request)
    if get_settings().auth_mode != "password":
        raise HTTPException(409, "未启用内置账号登录")
    async with crypto_slots:
        encoded = await asyncio.to_thread(password_hash, data.password.get_secret_value())
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('begin-agent-initial-admin'))")
    )
    if not await setup_required(session):
        raise HTTPException(409, "平台已初始化，请登录")
    username = data.username.lower()
    if await session.scalar(select(PlatformUser).where(PlatformUser.username == username)):
        raise HTTPException(409, "用户名已存在")
    user = PlatformUser(
        user_code=new_code("usr"),
        external_user_id="account:" + username,
        username=username,
        display_name=data.display_name,
        password_hash=encoded,
        platform_admin=True,
    )
    session.add(user)
    tenants = (await session.scalars(select(Tenant))).all()
    if not tenants:
        tenant = Tenant(tenant_code=new_code("ten"), tenant_name="我的工作空间")
        session.add(tenant)
        await session.flush()
        tenants = [tenant]
    for tenant in tenants:
        session.add(
            TenantMember(
                tenant_code=tenant.tenant_code,
                member_code=new_code("mem"),
                user_code=user.user_code,
                role_code="TENANT_ADMIN",
            )
        )
    await session.flush()
    token = await new_session(session, user)
    await session.commit()
    return set_cookie(JSONResponse(jsonable_encoder(public(user)), status_code=201), token, request)


async def reserve_attempt(session, username, client_ip):
    keys = []
    for value, maximum in (("user:" + username, 8), ("ip:" + client_ip, 30)):
        key, now = digest(value), datetime.now(UTC)
        stmt = insert(LoginBucket).values(
            bucket_key=key, count=1, expires_at=now + timedelta(minutes=15)
        )
        result = await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["bucket_key"],
                set_={
                    "count": case(
                        (LoginBucket.expires_at > now, func.least(LoginBucket.count + 1, 1000)),
                        else_=1,
                    ),
                    "expires_at": case(
                        (LoginBucket.expires_at > now, LoginBucket.expires_at),
                        else_=now + timedelta(minutes=15),
                    ),
                },
            ).returning(LoginBucket.count)
        )
        count = result.scalar_one()
        keys.append(key)
        if count > maximum:
            await session.commit()
            raise HTTPException(
                429, "登录失败次数过多，请 15 分钟后重试", headers={"Retry-After": "900"}
            )
    await session.commit()
    return keys


@accounts_router.post("/login")
async def login(data: LoginInput, request: Request, session=Depends(database_session)):
    same_origin(request)
    if get_settings().auth_mode != "password":
        raise HTTPException(409, "未启用内置账号登录")
    username = data.username.lower()
    keys = await reserve_attempt(
        session, username, request.client.host if request.client else "unknown"
    )
    user = await session.scalar(select(PlatformUser).where(PlatformUser.username == username))
    encoded = user.password_hash if user and user.password_hash else DUMMY_HASH
    async with crypto_slots:
        valid = await asyncio.to_thread(verify_password, data.password.get_secret_value(), encoded)
    if not valid or not user or user.status != 1:
        raise HTTPException(401, "用户名或密码错误，或账号已停用")
    user = await session.scalar(
        select(PlatformUser)
        .where(PlatformUser.user_code == user.user_code)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user.password_hash != encoded or user.status != 1:
        raise HTTPException(401, "账号状态已变更，请重新登录")
    for key in keys:
        await session.execute(
            update(LoginBucket)
            .where(LoginBucket.bucket_key == key, LoginBucket.expires_at > func.now())
            .values(count=func.greatest(LoginBucket.count - 1, 0))
        )
    token = await new_session(session, user)
    await session.commit()
    return set_cookie(JSONResponse(jsonable_encoder(public(user))), token, request)


@accounts_router.post("/logout")
async def logout(request: Request, session=Depends(database_session)):
    same_origin(request)
    await session.execute(
        delete(AccountSession).where(AccountSession.token_hash == digest(credential(request)))
    )
    await session.commit()
    return set_cookie(JSONResponse({"loggedOut": True}), "", request, age=0)


@accounts_router.post("/password")
async def change_password(data: PasswordInput, request: Request, session=Depends(database_session)):
    same_origin(request)
    from app.api.auth import current_principal

    p = current_principal(request)
    user = await session.scalar(select(PlatformUser).where(PlatformUser.user_code == p.user_code))
    old_hash = user.password_hash
    async with crypto_slots:
        valid = await asyncio.to_thread(
            verify_password, data.old_password.get_secret_value(), user.password_hash
        )
        if not valid:
            raise HTTPException(403, "原密码错误")
        encoded = await asyncio.to_thread(password_hash, data.password.get_secret_value())
    user = await session.scalar(
        select(PlatformUser)
        .where(PlatformUser.user_code == p.user_code)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user.password_hash != old_hash:
        raise HTTPException(409, "密码已变更，请重新验证")
    user.password_hash = encoded
    await session.execute(
        delete(AccountSession).where(
            AccountSession.user_code == user.user_code,
            AccountSession.token_hash != digest(credential(request)),
        )
    )
    await session.commit()
    return {"changed": True}


@accounts_router.post("/logout-all")
async def logout_all(request: Request, session=Depends(database_session)):
    from app.api.auth import current_principal

    p = current_principal(request)
    await session.execute(delete(AccountSession).where(AccountSession.user_code == p.user_code))
    await session.commit()
    return set_cookie(JSONResponse({"loggedOut": True}), "", request, age=0)


async def create_account(data, p, session):
    if not p.platform_admin:
        raise HTTPException(403, "需要平台管理员权限")
    username = data.username.lower()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('begin-agent-account-management'))")
    )
    if await session.scalar(
        select(PlatformUser.user_code).where(PlatformUser.username == username)
    ):
        raise HTTPException(409, "用户名已存在")
    async with crypto_slots:
        encoded = await asyncio.to_thread(password_hash, data.password.get_secret_value())
    user = PlatformUser(
        user_code=new_code("usr"),
        external_user_id="account:" + username,
        username=username,
        display_name=data.display_name,
        password_hash=encoded,
        platform_admin=data.platform_admin,
    )
    session.add(user)
    await session.commit()
    return public(user)


async def reset_account_password(code, data, p, session):
    if not p.platform_admin:
        raise HTTPException(403, "需要平台管理员权限")
    row = await session.scalar(
        select(PlatformUser).where(PlatformUser.user_code == code).with_for_update()
    )
    if not row or not row.username:
        raise HTTPException(404, "内置账号不存在")
    async with crypto_slots:
        row.password_hash = await asyncio.to_thread(password_hash, data.password.get_secret_value())
    await session.execute(delete(AccountSession).where(AccountSession.user_code == code))
    await session.commit()
    return {"changed": True, "sessions_revoked": True}
