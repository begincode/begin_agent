"""Tenant-scoped role directory with bounded queries and member counts."""

from sqlalchemy import and_, func, select

from app.core.identity import Principal
from app.models.platform import PlatformUser, TenantMember
from app.models.workspace import RoleDefinition
from app.repositories.platform import public
from app.services.members import builtins
from app.services.permissions import PERMISSIONS, allowed, effective_permissions


async def role_page(repo, principal: Principal, search="", status=None, limit=50, offset=0):
    builtin = [
        r
        for r in builtins()
        if (status is None or r["status"] == status)
        and (not search or search.casefold() in (r["role_name"] + r["role_code"]).casefold())
    ]
    query = repo.query(RoleDefinition)
    if search:
        query = query.where(
            RoleDefinition.role_name.icontains(search, autoescape=True)
            | RoleDefinition.role_code.icontains(search, autoescape=True)
        )
    if status is not None:
        query = query.where(RoleDefinition.status == status)
    selected = builtin[offset : offset + limit]
    remaining = limit - len(selected)
    if remaining:
        custom = await repo.session.scalars(
            query.order_by(RoleDefinition.created_at.desc(), RoleDefinition.role_code.desc())
            .offset(max(0, offset - len(builtin)))
            .limit(remaining)
        )
        selected += [public(r) for r in custom]
    codes = [r["role_code"] for r in selected]
    counts = {}
    can_count = allowed(principal, "members.manage")
    if codes and can_count:
        counts = {
            code: (total, active)
            for code, total, active in (
                await repo.session.execute(
                    select(
                        TenantMember.role_code,
                        func.count(),
                        func.count().filter(
                            and_(TenantMember.status == 1, PlatformUser.status == 1)
                        ),
                    )
                    .join(PlatformUser, PlatformUser.user_code == TenantMember.user_code)
                    .where(
                        TenantMember.tenant_code == repo.tenant_code,
                        TenantMember.role_code.in_(codes),
                    )
                    .group_by(TenantMember.role_code)
                )
            ).all()
        }
    own = effective_permissions(principal)
    for row in selected:
        reason = ""
        if row.get("builtin"):
            reason = "内置角色只读"
        elif set(row["permissions"]) - own:
            reason = "该角色包含你不能管理的权限"
        elif not principal.tenant_active:
            reason = "当前租户已停用，仅可查看"
        row.update(
            member_count=counts.get(row["role_code"], (0, 0))[0] if can_count else None,
            active_member_count=counts.get(row["role_code"], (0, 0))[1] if can_count else None,
            editable=not reason,
            blocked_reason=reason,
        )
    return {"roles": selected, "permissions": PERMISSIONS}
