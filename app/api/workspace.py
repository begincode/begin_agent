import os
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.accounts import (
    CreateUserInput,
    ResetPasswordInput,
    create_account,
    reset_account_password,
)
from app.api.auth import current_principal
from app.api.platform import scope
from app.core.config import get_settings
from app.core.credentials import CredentialCipher
from app.core.identity import Principal, new_code
from app.domain.platform import ToolInput, VersionInput
from app.domain.workspace import (
    DebugInput,
    DraftInput,
    GrantInput,
    McpInput,
    RerunInput,
    RoleInput,
    RollbackInput,
    SkillInput,
    TenantNameInput,
    TestCaseInput,
    TurnInput,
    UserInput,
)
from app.models.platform import (
    Agent,
    AgentRun,
    AgentTask,
    AgentVersion,
    PlatformUser,
    Tenant,
    TenantMember,
    ToolDefinition,
)
from app.models.workspace import (
    AccountSession,
    AgentDraft,
    AgentTestCase,
    AuditEvent,
    McpConnection,
    ResourceGrant,
    RoleDefinition,
    SkillDefinition,
    TaskTurn,
)
from app.repositories.platform import database_session, public
from app.services.conversations import append_turn, enqueue
from app.services.permissions import (
    PERMISSIONS,
    allowed,
    effective_permissions,
    require,
)
from app.services.platform import AgentService
from app.services.resources import RESOURCE_MODELS, list_resources, resource_page, resource_totals
from app.tools.builtins import CATALOG
from app.tools.mcp import mcp_request, validate_mcp_endpoint

workspace_router = APIRouter(prefix="/api/v1", tags=["workspace"])
S = Depends(database_session)
P = Depends(current_principal)


@workspace_router.get("/overview")
async def overview(days: int = Query(7, ge=1, le=30), p: Principal = P, session: AsyncSession = S):
    await scope(p, session)

    async def count(model, *conditions):
        return await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.tenant_code == p.tenant_code, *conditions)
        )

    tasks_visible = allowed(p, "tasks.read")
    from app.services.statistics import activity_statistics

    activity = await activity_statistics(
        session, await scope(p, session), days, tasks_visible, allowed(p, "runs.read")
    )
    return {
        "activity": activity,
        "agents": await count(Agent) if allowed(p, "agents.read") else None,
        "tasks": await count(AgentTask) if tasks_visible else None,
        "running": await count(AgentTask, AgentTask.status.in_(["RUNNING", "PENDING"]))
        if tasks_visible
        else None,
        "succeeded": await count(AgentTask, AgentTask.status == "SUCCEEDED")
        if tasks_visible
        else None,
        "failed": await count(AgentTask, AgentTask.status.in_(["FAILED", "TIMEOUT"]))
        if tasks_visible
        else None,
        "recent_tasks": [
            public(r)
            for r in (
                await (await scope(p, session)).list(AgentTask, limit=5) if tasks_visible else []
            )
        ],
    }


@workspace_router.get("/tenant")
async def current_tenant(p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, permission="tenant.manage")
    from app.services.tenants import tenant_snapshot

    return await tenant_snapshot(repo, p)


@workspace_router.patch("/tenant")
async def rename_current_tenant(data: TenantNameInput, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, permission="tenant.manage", write=True)
    tenant = await repo.get(Tenant, "tenant_code", p.tenant_code, lock=True)
    tenant.tenant_name = data.tenant_name
    await session.commit()
    return public(tenant)


@workspace_router.get("/runs")
async def runs(
    search: str = Query("", max_length=200),
    status: str = "",
    task_code: str = Query("", max_length=40),
    agent_code: str = Query("", max_length=40),
    days: int = Query(0, ge=0, le=90),
    root_only: bool = False,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = P,
    session: AsyncSession = S,
):
    repo = await scope(p, session)
    query = repo.query(AgentRun)
    if search:
        matches = AgentRun.run_code.contains(search, autoescape=True) | AgentRun.task_code.contains(
            search, autoescape=True
        )
        if allowed(p, "tasks.read"):
            matches |= AgentRun.task_code.in_(
                select(AgentTask.task_code).where(
                    AgentTask.tenant_code == p.tenant_code,
                    AgentTask.title.contains(search, autoescape=True),
                )
            )
        if allowed(p, "agents.read"):
            matches |= AgentRun.agent_code.in_(
                select(Agent.agent_code).where(
                    Agent.tenant_code == p.tenant_code,
                    Agent.agent_name.contains(search, autoescape=True),
                )
            )
        query = query.where(matches)
    if status:
        query = query.where(AgentRun.status == status)
    if task_code:
        query = query.where(AgentRun.task_code == task_code)
    if agent_code:
        query = query.where(AgentRun.agent_code == agent_code)
    if root_only:
        query = query.where(AgentRun.parent_run_code.is_(None))
    if days:
        from datetime import time, timedelta

        from app.services.statistics import ZONE, date_window

        _, today, boundary = date_window(days)
        end = datetime.combine(today + timedelta(days=1), time.min, ZONE)
        query = query.where(AgentRun.created_at >= boundary, AgentRun.created_at < end)
    rows = (
        await session.scalars(
            query.order_by(AgentRun.created_at.desc(), AgentRun.run_code.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    from app.services.execution import run_summaries

    return await run_summaries(
        session, repo, rows, allowed(p, "tasks.read"), allowed(p, "agents.read")
    )


@workspace_router.get("/tasks/{code}/stream")
async def task_stream(code: str, request: Request, p: Principal = P):
    from app.api import auth
    from app.services.task_stream import events, snapshot

    initial = await snapshot(auth.session_factory, p, code)
    return StreamingResponse(
        events(request, auth.session_factory, p, code, initial),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@workspace_router.get("/tasks/{code}/execution")
async def execution(code: str, p: Principal = P, session: AsyncSession = S):
    require(p, "runs.read")
    repo = await scope(p, session)
    task = await repo.get(AgentTask, "task_code", code)
    from app.services.execution import summarize_execution

    return await summarize_execution(session, repo, task)


@workspace_router.get("/agents/{code}")
async def agent_detail(code: str, p: Principal = P, session: AsyncSession = S):
    return public(await (await scope(p, session)).get(Agent, "agent_code", code))


@workspace_router.get("/agents/{code}/draft")
async def draft(code: str, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session)
    await repo.get(Agent, "agent_code", code)
    row = await session.scalar(repo.query(AgentDraft).where(AgentDraft.agent_code == code))
    return public(row) if row else {"agent_code": code, "config": None, "revision": 0}


@workspace_router.put("/agents/{code}/draft")
async def save_draft(code: str, data: DraftInput, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, permission="agents.manage", write=True)
    await repo.get(Agent, "agent_code", code, lock=True)
    row = await session.scalar(repo.query(AgentDraft).where(AgentDraft.agent_code == code))
    if (row.revision if row else 0) != data.revision:
        raise HTTPException(409, "草稿已被其他操作更新，请重新加载")
    if row is None:
        row = AgentDraft(
            tenant_code=p.tenant_code, draft_code=new_code("dft"), agent_code=code, revision=0
        )
        session.add(row)
    row.config = data.model_dump(exclude={"revision"})
    row.revision += 1
    await session.commit()
    await session.refresh(row)
    return public(row)


@workspace_router.post("/agents/{code}/rollback")
async def rollback(code: str, data: RollbackInput, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, permission="agents.publish", write=True)
    agent = await repo.get(Agent, "agent_code", code, lock=True)
    version = await repo.get(AgentVersion, "version_code", data.version_code)
    if version.agent_code != code or version.status != "PUBLISHED":
        raise HTTPException(409, "只能回滚到本 Agent 已发布的历史版本")
    agent.current_version_code = version.version_code
    await session.commit()
    return public(agent)


@workspace_router.post("/agents/{code}/debug", status_code=201)
async def debug(code: str, data: DebugInput, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, write=True)
    require(p, "tasks.create")
    agent = await repo.get(Agent, "agent_code", code)
    version_code = data.version_code or agent.current_version_code
    if data.use_draft:
        require(p, "agents.manage")
        row = await repo.get(AgentDraft, "agent_code", code)
        from pydantic import ValidationError

        try:
            config = VersionInput.model_validate(row.config)
        except ValidationError:
            raise HTTPException(422, "草稿缺少模型或有效提示词，请先完善配置") from None
        version = await AgentService(session, p.tenant_code).publish(code, config, activate=False)
        version_code = version["version_code"]
    if not version_code:
        raise HTTPException(409, "请先保存可执行草稿或发布版本")
    row = await enqueue(session, p, code, version_code, "调试 · " + agent.agent_name, data.prompt)
    return public(row)


@workspace_router.post("/tasks/{code}/messages", status_code=201)
async def add_message(code: str, data: TurnInput, p: Principal = P, session: AsyncSession = S):
    await scope(p, session, write=True)
    task, turn = await append_turn(session, p, code, data)
    return {"task": public(task), "turn": public(turn)}


@workspace_router.post("/tasks/{code}/retry", status_code=201)
async def retry(code: str, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, write=True)
    task = await repo.get(AgentTask, "task_code", code)
    task, turn = await append_turn(
        session, p, code, TurnInput(prompt=task.input["prompt"]), retry=True
    )
    return {"task": public(task), "turn": public(turn)}


@workspace_router.post("/tasks/{code}/rerun", status_code=201)
async def rerun(code: str, data: RerunInput, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, write=True)
    source = await repo.get(TaskTurn, "turn_code", data.source_turn_code)
    if source.task_code != code:
        raise HTTPException(404, "轮次不存在")
    task, turn = await append_turn(
        session,
        p,
        code,
        TurnInput(prompt=source.prompt, idempotency_key=data.idempotency_key),
        rerun_from=source.turn_code,
    )
    return {"task": public(task), "turn": public(turn)}


@workspace_router.get("/agents/{code}/test-cases")
async def test_cases(code: str, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session)
    await repo.get(Agent, "agent_code", code)
    cases = await repo.list(AgentTestCase, agent_code=code)
    latest = {}
    if cases and allowed(p, "tasks.read"):
        case_ref = AgentTask.input["test_case_code"].astext
        tasks = (
            await session.scalars(
                repo.query(AgentTask)
                .where(AgentTask.agent_code == code, case_ref.in_([c.case_code for c in cases]))
                .distinct(case_ref)
                .order_by(case_ref, AgentTask.created_at.desc(), AgentTask.task_code.desc())
            )
        ).all()
        latest = {
            t.input["test_case_code"]: {
                "task_code": t.task_code,
                "version_code": t.agent_version_code,
                "status": t.status,
                "created_at": t.created_at,
                "finished_at": t.finished_at,
                "test_result": (t.output or {}).get("test_result"),
            }
            for t in tasks
        }
    return [{**public(c), "latest_execution": latest.get(c.case_code)} for c in cases]


@workspace_router.post("/agents/{code}/test-cases", status_code=201)
async def create_case(code: str, data: TestCaseInput, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, permission="agents.manage", write=True)
    await repo.get(Agent, "agent_code", code)
    row = AgentTestCase(
        tenant_code=p.tenant_code, case_code=new_code("tcs"), agent_code=code, **data.model_dump()
    )
    session.add(row)
    await session.commit()
    return public(row)


@workspace_router.patch("/agents/{code}/test-cases/{case}")
async def edit_case(
    code: str, case: str, data: TestCaseInput, p: Principal = P, session: AsyncSession = S
):
    repo = await scope(p, session, permission="agents.manage", write=True)
    row = await repo.get(AgentTestCase, "case_code", case, lock=True)
    if row.agent_code != code:
        raise HTTPException(404, "用例不存在")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    await session.commit()
    return public(row)


@workspace_router.post("/agents/{code}/test-cases/{case}/run", status_code=201)
async def run_case(
    code: str,
    case: str,
    data: RollbackInput | None = None,
    p: Principal = P,
    session: AsyncSession = S,
):
    repo = await scope(p, session, write=True)
    require(p, "tasks.create")
    row = await repo.get(AgentTestCase, "case_code", case)
    agent = await repo.get(Agent, "agent_code", code)
    if row.agent_code != code or row.status != 1:
        raise HTTPException(409, "测试用例不可执行")
    version = data.version_code if data else agent.current_version_code
    if not version:
        raise HTTPException(409, "请先发布版本")
    task = await enqueue(
        session,
        p,
        code,
        version,
        "测试 · " + row.case_name,
        row.prompt,
        {"test_case_code": case, "expected_contains": row.expected_contains},
    )
    return public(task)


@workspace_router.get("/tools/{code}")
async def tool_detail(code: str, p: Principal = P, session: AsyncSession = S):
    return public(await (await scope(p, session)).get(ToolDefinition, "tool_code", code))


@workspace_router.get("/mcps/{code}")
async def mcp_detail(code: str, p: Principal = P, session: AsyncSession = S):
    return mcp_public(await (await scope(p, session)).get(McpConnection, "mcp_code", code))


@workspace_router.get("/skills/{code}")
async def skill_detail(code: str, p: Principal = P, session: AsyncSession = S):
    return public(await (await scope(p, session)).get(SkillDefinition, "skill_code", code))


@workspace_router.patch("/tools/{code}")
async def edit_tool(code: str, data: ToolInput, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, permission="resources.manage", write=True)
    row = await repo.get(ToolDefinition, "tool_code", code, lock=True)
    row.tool_name, row.group_name, row.status = data.tool_name, data.group_name, data.status
    row.input_schema, row.connection_config = (
        CATALOG[data.implementation],
        {"implementation": data.implementation},
    )
    await session.commit()
    return public(row)


@workspace_router.get("/mcps")
async def mcps(
    search: str = Query("", max_length=200),
    status: int | None = Query(None, ge=0, le=1),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = P,
    session: AsyncSession = S,
):
    repo = await scope(p, session)
    return [
        mcp_public(r)
        for r in await list_resources(
            repo, McpConnection, "mcp_name", search, status, limit, offset
        )
    ]


def mcp_public(row):
    return {**public(row), "has_token": bool(row.token_ciphertext)}


async def save_mcp(data, p, session, code=None):
    repo = await scope(p, session, permission="resources.manage", write=True)
    endpoint = validate_mcp_endpoint(data.endpoint)
    row = (
        await repo.get(McpConnection, "mcp_code", code, lock=True)
        if code
        else McpConnection(tenant_code=p.tenant_code, mcp_code=new_code("mcp"), tools=[])
    )
    if code and row.endpoint != endpoint:
        # Credentials are origin-bound; moving a connection cannot silently reuse its secret.
        row.token_ciphertext, row.tools, row.checked_at = None, [], None
    row.mcp_name, row.endpoint, row.status = data.mcp_name, endpoint, data.status
    if data.clear_token:
        row.token_ciphertext = None
    if data.token:
        row.token_ciphertext = CredentialCipher(get_settings()).encrypt(
            data.token.get_secret_value()
        )
    if data.clear_token or data.token:
        row.tools, row.checked_at = [], None
    session.add(row)
    await session.commit()
    return mcp_public(row)


@workspace_router.post("/mcps", status_code=201)
async def create_mcp(data: McpInput, p: Principal = P, session: AsyncSession = S):
    return await save_mcp(data, p, session)


@workspace_router.patch("/mcps/{code}")
async def edit_mcp(code: str, data: McpInput, p: Principal = P, session: AsyncSession = S):
    return await save_mcp(data, p, session, code)


@workspace_router.post("/mcps/{code}/check")
async def check_mcp(code: str, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, permission="resources.manage", write=True)
    row = await repo.get(McpConnection, "mcp_code", code)
    if row.status != 1:
        raise HTTPException(409, "MCP 已停用")
    endpoint, credential = row.endpoint, row.token_ciphertext
    await session.rollback()  # Network discovery never holds a DB transaction.
    token = CredentialCipher(get_settings()).decrypt(credential) if credential else None
    try:
        catalog = await mcp_request(endpoint, token)
    except (Exception, BaseExceptionGroup):
        raise HTTPException(502, "MCP 连接或工具发现失败，请检查地址和凭据") from None
    row = await repo.get(McpConnection, "mcp_code", code, lock=True)
    if row.endpoint != endpoint or row.token_ciphertext != credential or row.status != 1:
        raise HTTPException(409, "连接配置已变更，请重新检测")
    row.tools, row.checked_at, row.last_error = catalog, datetime.now(UTC), None
    await session.commit()
    return mcp_public(row)


@workspace_router.get("/skills")
async def skills(
    search: str = Query("", max_length=200),
    status: int | None = Query(None, ge=0, le=1),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = P,
    session: AsyncSession = S,
):
    repo = await scope(p, session)
    return [
        public(r)
        for r in await list_resources(
            repo, SkillDefinition, "skill_name", search, status, limit, offset
        )
    ]


@workspace_router.post("/skills", status_code=201)
async def create_skill(data: SkillInput, p: Principal = P, session: AsyncSession = S):
    await scope(p, session, permission="resources.manage", write=True)
    row = SkillDefinition(
        tenant_code=p.tenant_code, skill_code=new_code("skl"), **data.model_dump()
    )
    session.add(row)
    await session.commit()
    return public(row)


@workspace_router.patch("/skills/{code}")
async def edit_skill(code: str, data: SkillInput, p: Principal = P, session: AsyncSession = S):
    row = await (await scope(p, session, permission="resources.manage", write=True)).get(
        SkillDefinition, "skill_code", code, lock=True
    )
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    await session.commit()
    return public(row)


@workspace_router.get("/resources")
async def resources(
    search: str = Query("", max_length=200),
    resource_kind: Literal["MODEL", "TOOL", "MCP", "SKILL"] | None = None,
    availability: Literal["AVAILABLE", "REVOKED", "INACTIVE", "UNCHECKED"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = P,
    session: AsyncSession = S,
):
    await scope(p, session)
    return await resource_page(
        session, p.tenant_code, search, resource_kind, availability, limit, offset
    )


@workspace_router.get("/resources/overview")
async def resources_overview(p: Principal = P, session: AsyncSession = S):
    await scope(p, session)
    tenant = await session.scalar(select(Tenant).where(Tenant.tenant_code == p.tenant_code))
    groups = await resource_totals(session, p.tenant_code)
    return {
        "tenant": public(tenant),
        "groups": groups,
        "total": sum(g["total"] for g in groups),
        "available": sum(g["available"] for g in groups),
        "members": await session.scalar(
            select(func.count())
            .select_from(TenantMember)
            .where(TenantMember.tenant_code == p.tenant_code)
        )
        if allowed(p, "members.manage")
        else None,
        "agents": await session.scalar(
            select(func.count()).select_from(Agent).where(Agent.tenant_code == p.tenant_code)
        )
        if allowed(p, "agents.read")
        else None,
    }


@workspace_router.put("/resources/grant")
async def grant(data: GrantInput, p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, permission="tenant.manage", write=True)
    model, field, _ = RESOURCE_MODELS[data.resource_kind]
    await repo.get(model, field, data.resource_code)
    stmt = insert(ResourceGrant).values(
        tenant_code=p.tenant_code, grant_code=new_code("grt"), **data.model_dump()
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_code", "resource_kind", "resource_code"],
            set_={"enabled": data.enabled},
        )
    )
    await session.commit()
    return data.model_dump()


@workspace_router.get("/roles")
async def roles(
    search: str = Query("", max_length=200),
    status: int | None = Query(None, ge=0, le=1),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = P,
    session: AsyncSession = S,
):
    repo = await scope(p, session, permission="roles.manage")
    from app.services.roles import role_page

    return await role_page(repo, p, search, status, limit, offset)


async def save_role(data, p, session, code=None):
    repo = await scope(p, session, permission="roles.manage", write=True)
    if set(data.permissions) - set(PERMISSIONS):
        raise HTTPException(422, "权限编码不存在")
    if set(data.permissions) - effective_permissions(p):
        raise HTTPException(403, "不能授予自己不具备的权限")
    row = (
        await repo.get(RoleDefinition, "role_code", code, lock=True)
        if code
        else RoleDefinition(tenant_code=p.tenant_code, role_code=new_code("rol"))
    )
    if row.role_code in {"TENANT_ADMIN", "MEMBER"}:
        raise HTTPException(409, "内置角色不可修改")
    if code and set(row.permissions) - effective_permissions(p):
        raise HTTPException(403, "该角色包含你不能管理的权限")
    if not p.platform_admin and row.role_code == p.role and data.status == 0:
        raise HTTPException(409, "不能停用当前使用的角色")
    row.role_name, row.permissions, row.status = (
        data.role_name,
        sorted(set(data.permissions)),
        data.status,
    )
    session.add(row)
    await session.commit()
    return public(row)


@workspace_router.post("/roles", status_code=201)
async def create_role(data: RoleInput, p: Principal = P, session: AsyncSession = S):
    return await save_role(data, p, session)


@workspace_router.patch("/roles/{code}")
async def edit_role(code: str, data: RoleInput, p: Principal = P, session: AsyncSession = S):
    return await save_role(data, p, session, code)


@workspace_router.get("/users")
async def users(
    search: str = Query("", max_length=200),
    status: int | None = Query(None, ge=0, le=1),
    platform_admin: bool | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = P,
    session: AsyncSession = S,
):
    if not p.platform_admin:
        raise HTTPException(403, "需要平台管理员权限")
    from app.services.users import user_page

    return await user_page(session, search, status, platform_admin, limit, offset)


@workspace_router.get("/users/{code}")
async def user_detail(code: str, p: Principal = P, session: AsyncSession = S):
    if not p.platform_admin:
        raise HTTPException(403, "需要平台管理员权限")
    from app.services.users import user_view

    row = await session.scalar(select(PlatformUser).where(PlatformUser.user_code == code))
    if not row:
        raise HTTPException(404, "用户不存在")
    return user_view(row)


@workspace_router.patch("/users/{code}")
async def edit_user(code: str, data: UserInput, p: Principal = P, session: AsyncSession = S):
    if not p.platform_admin:
        raise HTTPException(403, "需要平台管理员权限")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('begin-agent-account-management'))")
    )
    row = await session.scalar(
        select(PlatformUser).where(PlatformUser.user_code == code).with_for_update()
    )
    if not row:
        raise HTTPException(404, "用户不存在")
    if code == p.user_code and data.status == 0:
        raise HTTPException(409, "不能停用当前登录账号")
    target_admin = row.platform_admin if data.platform_admin is None else data.platform_admin
    if row.platform_admin and row.status == 1 and (data.status == 0 or not target_admin):
        count = await session.scalar(
            select(func.count())
            .select_from(PlatformUser)
            .where(
                PlatformUser.platform_admin.is_(True),
                PlatformUser.status == 1,
                PlatformUser.password_hash.is_not(None),
            )
        )
        if count <= 1:
            raise HTTPException(409, "不能移除最后一位平台管理员")
    row.display_name, row.status, row.platform_admin = data.display_name, data.status, target_admin
    if data.status == 0:
        await session.execute(delete(AccountSession).where(AccountSession.user_code == code))
    await session.commit()
    return public(row)


@workspace_router.post("/users", status_code=201)
async def create_user(data: CreateUserInput, p: Principal = P, session: AsyncSession = S):
    return await create_account(data, p, session)


@workspace_router.post("/users/{code}/password")
async def reset_user_password(
    code: str, data: ResetPasswordInput, p: Principal = P, session: AsyncSession = S
):
    return await reset_account_password(code, data, p, session)


@workspace_router.get("/audit/actors")
async def audit_actors(p: Principal = P, session: AsyncSession = S):
    repo = await scope(p, session, permission="audit.read")
    rows = (
        await session.execute(
            repo.query(AuditEvent)
            .outerjoin(PlatformUser, PlatformUser.user_code == AuditEvent.actor_code)
            .with_only_columns(AuditEvent.actor_code, PlatformUser.display_name)
            .distinct()
            .order_by(AuditEvent.actor_code)
        )
    ).all()
    return [{"user_code": code, "display_name": name or code} for code, name in rows]


@workspace_router.get("/audit")
async def audit(
    search: str = Query("", max_length=100),
    actor_code: str = Query("", max_length=40),
    days: int = Query(0, ge=0, le=90),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = P,
    session: AsyncSession = S,
):
    from app.services.statistics import action_name, date_window

    repo = await scope(p, session, permission="audit.read")
    query = (
        repo.query(AuditEvent)
        .outerjoin(PlatformUser, PlatformUser.user_code == AuditEvent.actor_code)
        .add_columns(PlatformUser.display_name)
    )
    if search:
        query = query.where(
            AuditEvent.action.contains(search)
            | AuditEvent.resource_code.contains(search)
            | PlatformUser.display_name.contains(search)
            | PlatformUser.username.contains(search)
        )
    if actor_code:
        query = query.where(AuditEvent.actor_code == actor_code)
    if days:
        query = query.where(AuditEvent.created_at >= date_window(days)[2])
    rows = (
        await session.execute(
            query.order_by(AuditEvent.created_at.desc(), AuditEvent.event_code.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        {
            **public(row),
            "actor_name": name or row.actor_code,
            "action_name": action_name(row.action),
        }
        for row, name in rows
    ]


@workspace_router.get("/integration-status")
async def integration_status(p: Principal = P):
    require(p, "tenant.manage")
    from app.workers.readiness import ready

    settings = get_settings()
    return {
        "accounts": {"enabled": settings.auth_mode == "password", "mode": settings.auth_mode},
        "pms": {"managed": bool(os.environ.get("PMS_APP_HOME")), "worker_ready": ready(settings)},
        "mcp_allowed_hosts": settings.mcp_allowed_hosts,
    }
