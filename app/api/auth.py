import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import replace
from urllib.parse import unquote, urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.identity import LOCAL_TENANT, LOCAL_USER, Principal, new_code, tenant_context
from app.db.session import session_factory
from app.models.platform import PlatformUser, Tenant, TenantMember
from app.models.workspace import AuditEvent, RoleDefinition
from app.services.permissions import request_permission, require

auth_router = APIRouter()
TOKEN = re.compile(r"sess_[A-Za-z0-9_-]{43}")
HANDOFF = re.compile(r"handoff_[A-Za-z0-9_-]{43}")
STATE = re.compile(r"[A-Za-z0-9_-]{43}")
COOKIE = "begin_agent_session"


class SsoRemote:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    async def call(self, path, token=None, body=None):
        s = self.settings
        basic = (
            "Basic "
            + base64.b64encode(
                f"{s.sso_client_id}:{s.sso_client_secret.get_secret_value()}".encode()
            ).decode()
        )
        headers = {
            "Authorization": f"Bearer {token}" if token else basic,
            "X-SSO-Client-Authorization": basic,
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=5, follow_redirects=False, transport=self.transport
            ) as client:
                response = await client.request(
                    "POST" if body is not None else "GET",
                    s.sso_server_url.rstrip("/") + path,
                    headers=headers,
                    json=body,
                )
            if response.status_code in {401, 403}:
                raise HTTPException(
                    response.status_code,
                    "登录已失效" if response.status_code == 401 else "当前账号未获系统访问授权",
                )
            if response.status_code != 200:
                raise ValueError("upstream failure")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid response")
            return data
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise HTTPException(503, "统一登录服务暂不可用") from exc

    async def validate(self, token):
        if not TOKEN.fullmatch(token):
            raise HTTPException(401, "登录凭证无效")
        data = await self.call("/me/profile", token)
        user_id = data.get("userId")
        if not isinstance(user_id, str) or not re.fullmatch(r"100[0-9]{16}", user_id):
            raise HTTPException(503, "统一登录服务响应无效")
        return user_id

    async def exchange(self, code, verifier):
        data = await self.call(
            "/auth/handoff/exchange",
            body={
                "clientId": self.settings.sso_client_id,
                "redirectUri": self.settings.sso_callback_uri,
                "code": code,
                "verifier": verifier,
            },
        )
        token, expiry = data.get("token", ""), data.get("expireTime", 0)
        if (
            not isinstance(token, str)
            or not TOKEN.fullmatch(token)
            or not isinstance(expiry, int)
            or expiry <= time.time() * 1000
        ):
            raise HTTPException(503, "统一登录服务响应无效")
        return token, expiry


def get_sso_remote():
    return SsoRemote(get_settings())


def safe_target(value):
    if not isinstance(value, str) or len(value) > 1500:
        return False
    for candidate in (value, unquote(value)):
        if (
            not candidate.startswith("/")
            or candidate.startswith("//")
            or "\\" in candidate
            or any(ord(c) < 32 or ord(c) == 127 for c in candidate)
        ):
            return False
        url = urlsplit(candidate)
        if url.scheme or url.netloc or "/sso/" in url.path:
            return False
    return True


def cookie(response, name, value, age):
    response.set_cookie(
        name,
        value,
        max_age=age,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def flow_cipher():
    key = hashlib.sha256(get_settings().sso_client_secret.get_secret_value().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


@auth_router.get("/sso/login")
async def login(request: Request):
    s = get_settings()
    if s.auth_mode != "sso":
        raise HTTPException(409, "本地模式无需统一登录")
    target = request.query_params.get("returnTo", request.scope.get("root_path", "") + "/")
    if len(request.query_params.getlist("returnTo")) > 1 or not safe_target(target):
        raise HTTPException(400, "登录回跳地址无效")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    url = (
        s.sso_server_url.rstrip("/")
        + "/login?"
        + urlencode(
            {
                "clientId": s.sso_client_id,
                "redirectUri": s.sso_callback_uri,
                "state": state,
                "challenge": challenge,
            }
        )
    )
    response = RedirectResponse(url, status_code=303)
    sealed = (
        flow_cipher()
        .encrypt(json.dumps({"state": state, "verifier": verifier, "target": target}).encode())
        .decode()
    )
    cookie(response, COOKIE + "_flow_" + state, sealed, 600)
    return response


@auth_router.get("/sso/callback")
async def callback(request: Request):
    if get_settings().auth_mode != "sso":
        raise HTTPException(409, "未启用统一登录")
    state, handoff = request.query_params.get("state", ""), request.query_params.get("handoff", "")
    if (
        not STATE.fullmatch(state)
        or not HANDOFF.fullmatch(handoff)
        or any(len(request.query_params.getlist(k)) != 1 for k in ("state", "handoff"))
    ):
        raise HTTPException(400, "登录回调无效")
    name = COOKIE + "_flow_" + state
    try:
        flow = json.loads(flow_cipher().decrypt(request.cookies.get(name, "").encode(), ttl=600))
        if flow["state"] != state or not safe_target(flow["target"]):
            raise ValueError("state mismatch")
    except (InvalidToken, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, "登录流程已过期或不匹配，请重新登录") from exc
    token, expiry = await get_sso_remote().exchange(handoff, flow["verifier"])
    response = RedirectResponse(flow["target"], status_code=303)
    cookie(response, COOKIE, token, max(0, int((expiry - time.time() * 1000) / 1000)))
    cookie(response, name, "", 0)
    return response


@auth_router.post("/sso/logout")
@auth_router.post("/sso/logout-all")
async def logout(request: Request):
    if get_settings().auth_mode == "sso":
        token = request.headers.get("authorization", "").removeprefix(
            "Bearer "
        ) or request.cookies.get(COOKIE)
        await get_sso_remote().call(
            "/auth/logout-all" if request.url.path.endswith("logout-all") else "/auth/logout",
            token,
            {},
        )
    response = JSONResponse({"loggedOut": True})
    cookie(response, COOKIE, "", 0)
    return response


@auth_router.get("/api/v1/auth/config")
async def auth_config(request: Request):
    prefix = request.scope.get("root_path", "")
    from app.api.accounts import setup_required

    settings = get_settings()
    pending_setup = False
    if settings.auth_mode == "password":
        async with session_factory() as session:
            pending_setup = await setup_required(session)
    return {
        "mode": settings.auth_mode,
        "setup_required": pending_setup,
        "login_url": prefix + "/#/login",
        "api_base": prefix + "/api/v1",
    }


async def resolve_identity(external_id, requested_tenant):
    s = get_settings()
    async with session_factory() as session:
        await session.execute(
            insert(PlatformUser)
            .values(
                user_code=new_code("usr"), external_user_id=external_id, display_name=external_id
            )
            .on_conflict_do_nothing(index_elements=["external_user_id"])
        )
        await session.commit()
        user = await session.scalar(
            select(PlatformUser).where(PlatformUser.external_user_id == external_id)
        )
        if not user or user.status != 1:
            raise HTTPException(403, "平台账号已停用")
        memberships = (
            await session.execute(
                select(TenantMember, Tenant)
                .join(Tenant, Tenant.tenant_code == TenantMember.tenant_code)
                .where(
                    TenantMember.user_code == user.user_code,
                    TenantMember.status == 1,
                )
                .order_by(Tenant.created_at)
            )
        ).all()
        admin = user.platform_admin or external_id in s.platform_admin_user_ids
        choices = {m.tenant_code: m.role_code for m, _ in memberships}
        active = {t.tenant_code: t.status == 1 for _, t in memberships}
        selected = requested_tenant or next(iter(choices), "")
        if selected and selected not in choices:
            tenant = await session.scalar(select(Tenant).where(Tenant.tenant_code == selected))
            if not admin or not tenant:
                raise HTTPException(403, "无该租户访问权限")
            active[selected] = tenant.status == 1
        role_code = "TENANT_ADMIN" if admin else choices.get(selected, "MEMBER")
        permissions = ()
        if selected and role_code not in {"TENANT_ADMIN", "MEMBER"}:
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.tenant_code', :tenant, true)"), {"tenant": selected}
            )
            role = await session.scalar(
                select(RoleDefinition).where(
                    RoleDefinition.tenant_code == selected,
                    RoleDefinition.role_code == role_code,
                    RoleDefinition.status == 1,
                )
            )
            if role is None:
                raise HTTPException(403, "成员角色已停用或不存在")
            permissions = tuple(role.permissions)
        return Principal(
            user.user_code,
            external_id,
            admin,
            selected,
            role_code,
            tenant_active=active.get(selected, True),
            permissions=permissions,
        )


class IdentityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        prefix = request.scope.get("root_path", "")
        path = request.url.path.removeprefix(prefix) if prefix else request.url.path
        protected = path.startswith("/api/v1/") and path != "/api/v1/auth/config"
        if path in {"/api/v1/auth/setup", "/api/v1/auth/login"}:
            protected = False
        protected = protected or path in {"/sso/logout", "/sso/logout-all"}
        if not protected:
            response = await call_next(request)
            if path.startswith("/sso/"):
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
            return response
        s = get_settings()
        try:
            if s.auth_mode == "password":
                from app.api.accounts import authenticate

                external_id = await authenticate(request, session_factory)
                principal = await resolve_identity(
                    external_id, request.headers.get("x-tenant-code")
                )
            elif s.auth_mode == "local":
                auth = request.headers.get("authorization", "")
                valid_admin = s.model_admin_token and secrets.compare_digest(
                    auth, "Bearer " + s.model_admin_token.get_secret_value()
                )
                local = request.client and request.client.host in {"127.0.0.1", "::1"}
                if not (local or valid_admin):
                    raise HTTPException(403, "本地模式仅允许本机访问")
                principal = Principal(LOCAL_USER, "local", True, LOCAL_TENANT, "TENANT_ADMIN")
                if requested := request.headers.get("x-tenant-code"):
                    async with session_factory() as session:
                        tenant = await session.scalar(
                            select(Tenant).where(Tenant.tenant_code == requested)
                        )
                    if not tenant:
                        raise HTTPException(403, "租户不存在")
                    principal = replace(
                        principal, tenant_code=requested, tenant_active=tenant.status == 1
                    )
            else:
                authorization = request.headers.get("authorization")
                bearer = authorization is not None
                token = (
                    authorization.removeprefix("Bearer ")
                    if bearer
                    else request.cookies.get(COOKIE, "")
                )
                if bearer and not authorization.startswith("Bearer "):
                    raise HTTPException(401, "登录凭证无效")
                if not bearer and request.method not in {"GET", "HEAD", "OPTIONS"}:
                    origin = request.headers.get("origin")
                    expected = urlsplit(s.sso_callback_uri)
                    if origin != f"{expected.scheme}://{expected.netloc}":
                        raise HTTPException(403, "请求来源校验失败")
                external_id = await get_sso_remote().validate(token)
                principal = await resolve_identity(
                    external_id, request.headers.get("x-tenant-code")
                )
            global_admin = principal.platform_admin and re.fullmatch(
                r"/api/v1/tenants(?:/[^/]+)?", path
            )
            cancellation = re.fullmatch(r"/api/v1/tasks/[^/]+/cancel", path)
            if (
                not principal.tenant_active
                and path.startswith("/api/v1/")
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and not global_admin
                and not cancellation
                and not path.startswith("/api/v1/auth/")
                and not (principal.platform_admin and path.startswith("/api/v1/users"))
            ):
                raise HTTPException(403, "租户已停用，仅允许查看历史记录")
            request.state.principal = principal
            permission = request_permission(request.method, path)
            if permission:
                require(principal, permission)
            ctx = tenant_context.set(principal.tenant_code)
            try:
                response = await call_next(request)
                if (
                    request.method not in {"GET", "HEAD", "OPTIONS"}
                    and response.status_code < 400
                    and path.startswith("/api/v1/")
                    and principal.tenant_code
                ):
                    async with session_factory() as session:
                        session.add(
                            AuditEvent(
                                tenant_code=principal.tenant_code,
                                event_code=new_code("aud"),
                                actor_code=principal.user_code,
                                action=request.method + " " + path,
                                resource_code=path.split("/")[-1],
                                details={"status": response.status_code},
                            )
                        )
                        await session.commit()
            finally:
                tenant_context.reset(ctx)
            response.headers["Cache-Control"] = "no-store"
            return response
        except HTTPException as exc:
            response = JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
            if exc.status_code == 401 and "authorization" not in request.headers:
                if s.auth_mode == "password":
                    from app.api.accounts import set_cookie

                    set_cookie(response, "", request, age=0)
                else:
                    cookie(response, COOKIE, "", 0)
            return response
        except SQLAlchemyError:
            return JSONResponse(
                {"detail": "身份与租户数据库暂不可用"},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )


def current_principal(request: Request) -> Principal:
    p = getattr(request.state, "principal", None)
    if not p:
        raise HTTPException(401, "请先登录")
    return p
