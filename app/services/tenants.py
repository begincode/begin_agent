"""Current tenant metadata with separately authorized aggregate counts."""

from sqlalchemy import func, select

from app.models.platform import Agent, PlatformUser, Tenant, TenantMember
from app.repositories.platform import public
from app.services.permissions import allowed
from app.services.resources import resource_totals


async def tenant_snapshot(repo, principal):
    session, code = repo.session, repo.tenant_code
    tenant = await repo.get(Tenant, "tenant_code", code)
    result = {
        "tenant": public(tenant),
        "members": None,
        "administrators": None,
        "agents": None,
        "published_agents": None,
        "resources": None,
        "available_resources": None,
    }
    if allowed(principal, "members.manage"):
        result["members"] = await session.scalar(
            select(func.count()).select_from(TenantMember).where(TenantMember.tenant_code == code)
        )
        result["administrators"] = await session.scalar(
            select(func.count())
            .select_from(TenantMember)
            .join(PlatformUser, PlatformUser.user_code == TenantMember.user_code)
            .where(
                TenantMember.tenant_code == code,
                TenantMember.role_code == "TENANT_ADMIN",
                TenantMember.status == 1,
                PlatformUser.status == 1,
            )
        )
    if allowed(principal, "agents.read"):
        result["agents"] = await session.scalar(
            select(func.count()).select_from(Agent).where(Agent.tenant_code == code)
        )
        result["published_agents"] = await session.scalar(
            select(func.count())
            .select_from(Agent)
            .where(Agent.tenant_code == code, Agent.current_version_code.is_not(None))
        )
    if allowed(principal, "resources.read"):
        groups = await resource_totals(session, code)
        result["resources"] = sum(g["total"] for g in groups)
        result["available_resources"] = sum(g["available"] for g in groups)
    return result
