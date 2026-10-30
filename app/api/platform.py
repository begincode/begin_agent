from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_principal
from app.core.identity import LOCAL_TENANT, LOCAL_USER, Principal, new_code
from app.domain.platform import (
    AgentInput,
    MemberInput,
    RelationInput,
    RelationSettings,
    TaskInput,
    TenantInput,
    ToolInput,
    VersionInput,
)
from app.models.platform import (
    Agent,
    AgentRelation,
    AgentRun,
    AgentTask,
    AgentVersion,
    PlatformUser,
    RunMessage,
    RunStep,
    TaskArtifact,
    Tenant,
    TenantMember,
    ToolDefinition,
)
from app.models.workspace import RoleDefinition, TaskTurn
from app.repositories.platform import TenantRepository, database_session, public
from app.services.permissions import MEMBER_PERMISSIONS, allowed, effective_permissions
from app.services.platform import AgentService
from app.tools.builtins import CATALOG

platform_router = APIRouter(prefix="/api/v1", tags=["platform"])
Session = Depends(database_session)
Identity = Depends(current_principal)


def platform_admin(p):
    if not p.platform_admin:
        raise HTTPException(403, "需要平台管理员权限")


async def scope(p, session, admin=False, write=False, permission=None):
    if not p.tenant_code:
        raise HTTPException(403, "请先加入租户")
    tenant = await session.scalar(select(Tenant).where(Tenant.tenant_code == p.tenant_code))
    if not tenant:
        raise HTTPException(403, "租户不存在")
    if write and tenant.status != 1:
        raise HTTPException(403, "租户已停用，仅允许查看历史记录")
    if permission and not allowed(p, permission):
        raise HTTPException(403, "没有此操作权限")
    if admin and not allowed(p, "tenant.manage"):
        raise HTTPException(403, "需要租户管理员权限")
    return TenantRepository(session, p.tenant_code)


async def local_bootstrap(session):
    await session.execute(
        insert(Tenant)
        .values(tenant_code=LOCAL_TENANT, tenant_name="本地开发租户")
        .on_conflict_do_nothing(index_elements=["tenant_code"])
    )
    await session.execute(
        insert(PlatformUser)
        .values(user_code=LOCAL_USER, external_user_id="local", display_name="本地开发者")
        .on_conflict_do_nothing(index_elements=["external_user_id"])
    )
    await session.execute(
        insert(TenantMember)
        .values(
            member_code=new_code("mem"),
            tenant_code=LOCAL_TENANT,
            user_code=LOCAL_USER,
            role_code="TENANT_ADMIN",
        )
        .on_conflict_do_nothing(index_elements=["tenant_code", "user_code"])
    )
    await session.commit()


@platform_router.get("/me")
async def me(p: Principal = Identity, session: AsyncSession = Session):
    if p.external_user_id == "local":
        await local_bootstrap(session)
    query = select(Tenant, TenantMember.role_code).outerjoin(
        TenantMember,
        (TenantMember.tenant_code == Tenant.tenant_code)
        & (TenantMember.user_code == p.user_code)
        & (TenantMember.status == 1),
    )
    if not p.platform_admin:
        query = query.where(TenantMember.user_code == p.user_code)
    rows = (await session.execute(query.order_by(Tenant.created_at))).all()
    role_name = {"TENANT_ADMIN": "租户管理员", "MEMBER": "成员"}.get(p.role)
    if p.platform_admin:
        role_name = "平台管理员"
    elif role_name is None:
        role_name = (
            await session.scalar(
                select(RoleDefinition.role_name).where(
                    RoleDefinition.tenant_code == p.tenant_code, RoleDefinition.role_code == p.role
                )
            )
            or "自定义角色"
        )
    return {
        "user_code": p.user_code,
        "username": (
            await session.scalar(
                select(PlatformUser.username).where(PlatformUser.user_code == p.user_code)
            )
        ),
        "external_user_id": p.external_user_id,
        "platform_admin": p.platform_admin,
        "tenant_code": p.tenant_code,
        "role": p.role,
        "role_name": role_name,
        "permissions": sorted(effective_permissions(p)),
        "tenants": [{**public(t), "role": role or "TENANT_ADMIN"} for t, role in rows],
    }


@platform_router.post("/tenants", status_code=201)
async def create_tenant(
    data: TenantInput, p: Principal = Identity, session: AsyncSession = Session
):
    platform_admin(p)
    row = Tenant(tenant_code=new_code("ten"), **data.model_dump())
    session.add(row)
    await session.flush()
    session.add(
        TenantMember(
            tenant_code=row.tenant_code,
            member_code=new_code("mem"),
            user_code=p.user_code,
            role_code="TENANT_ADMIN",
        )
    )
    await session.commit()
    return public(row)


@platform_router.patch("/tenants/{code}")
async def edit_tenant(
    code: str, data: TenantInput, p: Principal = Identity, session: AsyncSession = Session
):
    platform_admin(p)
    row = await session.scalar(select(Tenant).where(Tenant.tenant_code == code).with_for_update())
    if not row:
        raise HTTPException(404, "租户不存在")
    row.tenant_name, row.status = data.tenant_name, data.status
    await session.commit()
    return public(row)


@platform_router.get("/members")
async def members(
    search: str = Query("", max_length=200),
    status: int | None = Query(None, ge=0, le=1),
    role_code: str = Query("", max_length=40),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = Identity,
    session: AsyncSession = Session,
):
    repo = await scope(p, session, permission="members.manage")
    from app.services.members import member_page

    return await member_page(repo, p, search, status, role_code, limit, offset)


@platform_router.get("/members/roles")
async def member_roles(p: Principal = Identity, session: AsyncSession = Session):
    repo = await scope(p, session, permission="members.manage")
    from app.services.members import assignment_roles

    return await assignment_roles(repo, p)


@platform_router.get("/members/{code}")
async def member_detail(code: str, p: Principal = Identity, session: AsyncSession = Session):
    repo = await scope(p, session, permission="members.manage")
    from app.services.members import member_query, member_view

    row = (
        await session.execute(member_query(repo).where(TenantMember.member_code == code))
    ).first()
    if row is None:
        raise HTTPException(404, "成员不存在")
    return member_view(row, p.tenant_active)


@platform_router.post("/members", status_code=201)
async def save_member(data: MemberInput, p: Principal = Identity, session: AsyncSession = Session):
    await scope(p, session, permission="members.manage", write=True)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 0))"),
        {"tenant": p.tenant_code},
    )
    if data.role_code not in {"TENANT_ADMIN", "MEMBER"}:
        role = await TenantRepository(session, p.tenant_code).get(
            RoleDefinition, "role_code", data.role_code
        )
        if role.status != 1:
            raise HTTPException(409, "角色已停用")
        target_permissions = set(role.permissions)
    else:
        target_permissions = (
            set(effective_permissions(p))
            if data.role_code == "TENANT_ADMIN"
            else set(MEMBER_PERMISSIONS)
        )
        if data.role_code == "TENANT_ADMIN" and not (p.platform_admin or p.role == "TENANT_ADMIN"):
            raise HTTPException(403, "不能授予租户管理员角色")
    if target_permissions - effective_permissions(p):
        raise HTTPException(403, "不能授予自己不具备的权限")
    if data.username or data.user_code:
        query = select(PlatformUser).where(PlatformUser.status == 1)
        query = (
            query.where(PlatformUser.user_code == data.user_code)
            if data.user_code
            else query.where(PlatformUser.username == data.username.lower())
        )
        user = await session.scalar(query)
        if not user:
            raise HTTPException(404, "账号不存在，请先由平台管理员创建用户")
    elif data.external_user_id:
        await session.execute(
            insert(PlatformUser)
            .values(
                user_code=new_code("usr"),
                external_user_id=data.external_user_id,
                display_name=data.display_name,
            )
            .on_conflict_do_nothing(index_elements=["external_user_id"])
        )
        user = await session.scalar(
            select(PlatformUser).where(PlatformUser.external_user_id == data.external_user_id)
        )
    else:
        raise HTTPException(422, "请提供已有用户名")
    row = await session.scalar(
        select(TenantMember)
        .where(TenantMember.tenant_code == p.tenant_code, TenantMember.user_code == user.user_code)
        .with_for_update()
    )
    if (
        row
        and row.role_code == "TENANT_ADMIN"
        and row.status == 1
        and (data.role_code != "TENANT_ADMIN" or data.status != 1)
    ):
        admins = (
            await session.scalars(
                select(TenantMember).where(
                    TenantMember.tenant_code == p.tenant_code,
                    TenantMember.role_code == "TENANT_ADMIN",
                    TenantMember.status == 1,
                )
            )
        ).all()
        if len(admins) <= 1:
            raise HTTPException(409, "不能移除租户最后一位管理员")
    if row is None:
        row = TenantMember(
            tenant_code=p.tenant_code, member_code=new_code("mem"), user_code=user.user_code
        )
        session.add(row)
    row.role_code, row.status = data.role_code, data.status
    await session.commit()
    return public(row)


@platform_router.get("/agents")
async def agents(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str = Query("", max_length=200),
    status: int | None = Query(None, ge=0, le=1),
    p: Principal = Identity,
    session: AsyncSession = Session,
):
    repo = await scope(p, session)
    query = repo.query(Agent)
    if search:
        query = query.where(Agent.agent_name.contains(search) | Agent.agent_code.contains(search))
    if status is not None:
        query = query.where(Agent.status == status)
    return [
        public(r)
        for r in (
            await session.scalars(
                query.order_by(Agent.created_at.desc(), Agent.agent_code.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    ]


@platform_router.post("/agents", status_code=201)
async def create_agent(data: AgentInput, p: Principal = Identity, session: AsyncSession = Session):
    await scope(p, session, permission="agents.manage", write=True)
    row = Agent(tenant_code=p.tenant_code, agent_code=new_code("agt"), **data.model_dump())
    session.add(row)
    await session.commit()
    return public(row)


@platform_router.patch("/agents/{code}")
async def edit_agent(
    code: str, data: AgentInput, p: Principal = Identity, session: AsyncSession = Session
):
    repo = await scope(p, session, permission="agents.manage", write=True)
    row = await repo.get(Agent, "agent_code", code, lock=True)
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    await session.commit()
    return public(row)


@platform_router.get("/agents/{code}/versions")
async def versions(code: str, p: Principal = Identity, session: AsyncSession = Session):
    repo = await scope(p, session)
    await repo.get(Agent, "agent_code", code)
    return [public(v) for v in await repo.list(AgentVersion, agent_code=code)]


@platform_router.post("/agents/{code}/versions", status_code=201)
async def publish(
    code: str, data: VersionInput, p: Principal = Identity, session: AsyncSession = Session
):
    await scope(p, session, permission="agents.publish", write=True)
    return await AgentService(session, p.tenant_code).publish(code, data)


@platform_router.get("/agents/{code}/relations")
async def relations(code: str, p: Principal = Identity, session: AsyncSession = Session):
    repo = await scope(p, session)
    await repo.get(Agent, "agent_code", code)
    rows = (
        await session.execute(
            repo.query(AgentRelation)
            .join(
                Agent,
                (Agent.agent_code == AgentRelation.sub_agent_code)
                & (Agent.tenant_code == AgentRelation.tenant_code),
            )
            .outerjoin(
                AgentVersion,
                (AgentVersion.version_code == Agent.current_version_code)
                & (AgentVersion.tenant_code == Agent.tenant_code),
            )
            .add_columns(
                Agent.agent_name, Agent.status, Agent.current_version_code, AgentVersion.version_no
            )
            .where(AgentRelation.master_agent_code == code)
            .order_by(AgentRelation.created_at, AgentRelation.relation_code)
            .limit(1000)
        )
    ).all()
    return [
        {
            **public(r),
            "sub_agent_name": name,
            "sub_agent_status": status,
            "sub_agent_version_code": version,
            "sub_agent_version_no": number,
        }
        for r, name, status, version, number in rows
    ]


@platform_router.post("/agents/{code}/relations", status_code=201)
async def relate(
    code: str, data: RelationInput, p: Principal = Identity, session: AsyncSession = Session
):
    await scope(p, session, permission="agents.manage", write=True)
    return await AgentService(session, p.tenant_code).relate(code, data)


@platform_router.patch("/agents/{code}/relations/{relation_code}")
async def edit_relation(
    code: str,
    relation_code: str,
    data: RelationSettings,
    p: Principal = Identity,
    session: AsyncSession = Session,
):
    await scope(p, session, permission="agents.manage", write=True)
    return await AgentService(session, p.tenant_code).relate(code, data, relation_code)


@platform_router.delete("/agents/{code}/relations/{relation_code}", status_code=204)
async def unrelate(
    code: str, relation_code: str, p: Principal = Identity, session: AsyncSession = Session
):
    repo = await scope(p, session, permission="agents.manage", write=True)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 0))"),
        {"tenant": p.tenant_code},
    )
    row = await repo.get(AgentRelation, "relation_code", relation_code, lock=True)
    if row.master_agent_code != code:
        raise HTTPException(404, "关系不存在")
    # Removing a draft relation cannot alter historical published snapshots.
    await session.delete(row)
    await session.commit()


@platform_router.get("/tools")
async def tools(
    search: str = Query("", max_length=200),
    status: int | None = Query(None, ge=0, le=1),
    group_name: str = Query("", max_length=64),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = Identity,
    session: AsyncSession = Session,
):
    from app.services.resources import list_resources

    repo = await scope(p, session)
    return [
        public(t)
        for t in await list_resources(
            repo, ToolDefinition, "tool_name", search, status, limit, offset, group_name
        )
    ]


@platform_router.get("/tools/groups")
async def tool_groups(p: Principal = Identity, session: AsyncSession = Session):
    repo = await scope(p, session)
    return list(
        (
            await session.scalars(
                repo.query(ToolDefinition)
                .with_only_columns(ToolDefinition.group_name)
                .distinct()
                .order_by(ToolDefinition.group_name)
            )
        ).all()
    )


@platform_router.post("/tools", status_code=201)
async def create_tool(data: ToolInput, p: Principal = Identity, session: AsyncSession = Session):
    await scope(p, session, permission="resources.manage", write=True)
    row = ToolDefinition(
        tenant_code=p.tenant_code,
        tool_code=new_code("tol"),
        tool_name=data.tool_name,
        group_name=data.group_name,
        status=data.status,
        input_schema=CATALOG[data.implementation],
        connection_config={"implementation": data.implementation},
    )
    session.add(row)
    await session.commit()
    return public(row)


@platform_router.get("/tasks")
async def tasks(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str = Query("", max_length=200),
    status: str = Query("", max_length=32),
    mine: bool = False,
    p: Principal = Identity,
    session: AsyncSession = Session,
):
    repo = await scope(p, session)
    query = repo.query(AgentTask)
    if mine:
        query = query.where(AgentTask.created_by == p.user_code)
    if search:
        query = query.where(AgentTask.title.contains(search) | AgentTask.task_code.contains(search))
    if status:
        query = query.where(AgentTask.status == status)
    return [
        public(r)
        for r in (
            await session.scalars(
                query.order_by(AgentTask.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
    ]


@platform_router.post("/tasks", status_code=201)
async def create_task(data: TaskInput, p: Principal = Identity, session: AsyncSession = Session):
    repo = await scope(p, session, write=True)
    agent = await repo.get(Agent, "agent_code", data.agent_code)
    if agent.status != 1 or not agent.current_version_code:
        raise HTTPException(409, "Agent 未启用或未发布版本")
    values = dict(
        tenant_code=p.tenant_code,
        task_code=new_code("tsk"),
        agent_code=agent.agent_code,
        agent_version_code=agent.current_version_code,
        title=data.title,
        input={"prompt": data.prompt},
        created_by=p.user_code,
        idempotency_key=data.idempotency_key,
        priority=data.priority,
        max_retries=data.max_retries,
    )
    result = await session.execute(
        insert(AgentTask)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["tenant_code", "idempotency_key"])
        .returning(AgentTask)
    )
    row = result.scalar_one_or_none()
    created = row is not None
    if row is None:
        row = await session.scalar(
            repo.query(AgentTask).where(AgentTask.idempotency_key == data.idempotency_key)
        )
        if (
            row.agent_code != data.agent_code
            or row.input.get("initial_prompt", row.input["prompt"]) != data.prompt
            or row.title != data.title
            or row.priority != data.priority
            or row.max_retries != data.max_retries
        ):
            raise HTTPException(409, "幂等键已用于不同任务")
    if created:
        session.add(
            TaskTurn(
                tenant_code=p.tenant_code,
                turn_code=new_code("trn"),
                task_code=row.task_code,
                turn_no=1,
                prompt=data.prompt,
                idempotency_key=data.idempotency_key,
            )
        )
    await session.commit()
    return public(row)


@platform_router.get("/tasks/{code}")
async def task_detail(code: str, p: Principal = Identity, session: AsyncSession = Session):
    repo = await scope(p, session)
    row = await repo.get(AgentTask, "task_code", code)
    runs = (
        (
            await session.scalars(
                repo.query(AgentRun).where(AgentRun.task_code == code).order_by(AgentRun.created_at)
            )
        ).all()
        if allowed(p, "runs.read")
        else []
    )
    artifacts = await repo.list(TaskArtifact, task_code=code)
    turns = (
        await session.scalars(
            repo.query(TaskTurn).where(TaskTurn.task_code == code).order_by(TaskTurn.turn_no)
        )
    ).all()
    from app.services.task_stream import turn_previews

    previews = await turn_previews(session, repo, row)
    turn_views = []
    for turn in turns:
        token, preview = previews.get(turn.turn_no, (None, None))
        if turn.status == "PENDING" or (turn.status == "RUNNING" and token != row.lease_token):
            preview = None
        elif preview and turn.status == "SUCCEEDED":
            preview = {**preview, "content": ""}  # The canonical text is already in turn.output.
        turn_views.append({**public(turn), "preview": preview})
    return {
        **public(row),
        "runs": [public(r) for r in runs],
        "artifacts": [public(a) for a in artifacts],
        "turns": turn_views,
    }


@platform_router.post("/tasks/{code}/cancel")
async def cancel(code: str, p: Principal = Identity, session: AsyncSession = Session):
    repo = await scope(p, session)
    row = await repo.get(AgentTask, "task_code", code, lock=True)
    if row.created_by != p.user_code and not allowed(p, "tasks.manage"):
        raise HTTPException(403, "只能取消自己的任务")
    if row.status in {"PENDING", "RUNNING"}:
        row.cancel_requested, row.status, row.error_code = True, "CANCELLED", "CANCELLED"
        row.finished_at = datetime.now(UTC)
        row.lease_token, row.lease_expires_at = None, None
        await session.execute(
            update(TaskTurn)
            .where(
                TaskTurn.tenant_code == p.tenant_code,
                TaskTurn.task_code == code,
                TaskTurn.status.in_(["PENDING", "RUNNING"]),
            )
            .values(status="CANCELLED", finished_at=datetime.now(UTC))
        )
        await session.execute(
            update(AgentRun)
            .where(
                AgentRun.tenant_code == p.tenant_code,
                AgentRun.task_code == code,
                AgentRun.status == "RUNNING",
            )
            .values(status="CANCELLED", finish_reason="CANCELLED", finished_at=datetime.now(UTC))
        )
        await session.commit()
    return public(row)


@platform_router.get("/runs/{code}")
async def run_detail(code: str, p: Principal = Identity, session: AsyncSession = Session):
    repo = await scope(p, session)
    run = await repo.get(AgentRun, "run_code", code)
    steps = (
        await session.scalars(
            repo.query(RunStep).where(RunStep.run_code == code).order_by(RunStep.step_no)
        )
    ).all()
    messages = (
        await session.scalars(
            repo.query(RunMessage)
            .where(RunMessage.run_code == code)
            .order_by(RunMessage.sequence_no)
        )
    ).all()
    from app.services.execution import run_summaries

    summary = (
        await run_summaries(
            session, repo, [run], allowed(p, "tasks.read"), allowed(p, "agents.read")
        )
    )[0]
    return {
        **public(run),
        **summary,
        "steps": [public(s) for s in steps],
        "messages": [{**public(m), "metadata": m.metadata_} for m in messages],
    }
