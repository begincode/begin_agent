from fastapi import HTTPException
from sqlalchemy import and_, case, func, literal, select, union_all

from app.models.model_profile import ModelProfile
from app.models.platform import ToolDefinition
from app.models.workspace import McpConnection, ResourceGrant, SkillDefinition
from app.repositories.platform import TenantRepository

RESOURCE_MODELS = {
    "MODEL": (ModelProfile, "profile_code", "display_name"),
    "TOOL": (ToolDefinition, "tool_code", "tool_name"),
    "MCP": (McpConnection, "mcp_code", "mcp_name"),
    "SKILL": (SkillDefinition, "skill_code", "skill_name"),
}


def resource_catalog(tenant):
    """Project public resource state in one query; never select credentials."""
    parts = []
    for kind, (model, code, name) in RESOURCE_MODELS.items():
        active = model.enabled if kind == "MODEL" else model.status == 1
        authorized = func.coalesce(ResourceGrant.enabled, True)
        ready = (
            and_(model.checked_at.is_not(None), func.jsonb_array_length(model.tools) > 0)
            if kind == "MCP"
            else literal(True)
        )
        state = case(
            (~authorized, "REVOKED"),
            (~active, "INACTIVE"),
            (~ready, "UNCHECKED"),
            else_="AVAILABLE",
        )
        parts.append(
            select(
                literal(kind).label("resource_kind"),
                getattr(model, code).label("resource_code"),
                getattr(model, name).label("resource_name"),
                model.created_at.label("created_at"),
                active.label("active"),
                authorized.label("enabled"),
                state.label("availability"),
                (func.jsonb_array_length(model.tools) if kind == "MCP" else literal(0)).label(
                    "tools_count"
                ),
            )
            .outerjoin(
                ResourceGrant,
                and_(
                    ResourceGrant.tenant_code == tenant,
                    ResourceGrant.resource_kind == kind,
                    ResourceGrant.resource_code == getattr(model, code),
                ),
            )
            .where(model.tenant_code == tenant)
        )
    return union_all(*parts).subquery("resource_catalog")


async def resource_page(
    session, tenant, search="", kind=None, availability=None, limit=50, offset=0
):
    catalog = resource_catalog(tenant)
    query = select(catalog)
    if search:
        query = query.where(
            catalog.c.resource_name.icontains(search, autoescape=True)
            | catalog.c.resource_code.contains(search, autoescape=True)
        )
    if kind:
        query = query.where(catalog.c.resource_kind == kind)
    if availability:
        query = query.where(catalog.c.availability == availability)
    rows = (
        (
            await session.execute(
                query.order_by(
                    catalog.c.resource_kind,
                    catalog.c.created_at.desc(),
                    catalog.c.resource_code,
                )
                .limit(limit)
                .offset(offset)
            )
        )
        .mappings()
        .all()
    )
    return [{**row, "available": row["availability"] == "AVAILABLE"} for row in rows]


async def resource_totals(session, tenant):
    catalog = resource_catalog(tenant)
    rows = (
        await session.execute(
            select(
                catalog.c.resource_kind, catalog.c.availability, func.count().label("count")
            ).group_by(catalog.c.resource_kind, catalog.c.availability)
        )
    ).all()
    groups = {
        kind: {
            "resource_kind": kind,
            "total": 0,
            "available": 0,
            "revoked": 0,
            "inactive": 0,
            "unchecked": 0,
        }
        for kind in RESOURCE_MODELS
    }
    for row in rows:
        groups[row.resource_kind]["total"] += row.count
        groups[row.resource_kind][row.availability.lower()] += row.count
    return list(groups.values())


async def resource_enabled(session, tenant, kind, code):
    grant = await session.scalar(
        select(ResourceGrant).where(
            ResourceGrant.tenant_code == tenant,
            ResourceGrant.resource_kind == kind,
            ResourceGrant.resource_code == code,
        )
    )
    return grant is None or grant.enabled


async def require_resource(session, tenant, kind, code):
    model, field, _ = RESOURCE_MODELS[kind]
    row = await TenantRepository(session, tenant).get(model, field, code)
    active = row.enabled if kind == "MODEL" else row.status == 1
    if not active or not await resource_enabled(session, tenant, kind, code):
        raise HTTPException(409, "资源已停用或被租户撤销授权")
    return row


async def list_resources(
    repo, model, name_field, search="", status=None, limit=50, offset=0, group_name=""
):
    query = repo.query(model)
    if search:
        condition = getattr(model, name_field).contains(search)
        for extra in ("group_name", "description", "endpoint"):
            if hasattr(model, extra):
                condition = condition | getattr(model, extra).contains(search)
        query = query.where(condition)
    if status is not None:
        query = query.where(model.status == status)
    if group_name:
        query = query.where(model.group_name == group_name)
    return (
        await repo.session.scalars(
            query.order_by(
                model.created_at.desc(),
                getattr(model, name_field),
                getattr(model, name_field.removesuffix("_name") + "_code"),
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
