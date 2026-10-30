"""Tenant member projections and role delegation metadata."""

from sqlalchemy import and_, case

from app.core.identity import Principal
from app.models.platform import PlatformUser, TenantMember
from app.models.workspace import RoleDefinition
from app.repositories.platform import public
from app.services.permissions import MEMBER_PERMISSIONS, PERMISSIONS, effective_permissions


def builtins():
    return [
        {
            "role_code": "TENANT_ADMIN",
            "role_name": "租户管理员",
            "permissions": sorted(PERMISSIONS),
            "status": 1,
            "builtin": True,
        },
        {
            "role_code": "MEMBER",
            "role_name": "成员",
            "permissions": sorted(MEMBER_PERMISSIONS),
            "status": 1,
            "builtin": True,
        },
    ]


async def assignment_roles(repo, principal):
    custom = (
        await repo.session.scalars(
            repo.query(RoleDefinition).order_by(RoleDefinition.created_at, RoleDefinition.role_code)
        )
    ).all()
    result = []
    for row in builtins() + [public(r) for r in custom]:
        reason = ""
        if row["status"] != 1:
            reason = "角色已停用"
        elif row["role_code"] == "TENANT_ADMIN" and not (
            principal.platform_admin or principal.role == "TENANT_ADMIN"
        ):
            reason = "仅管理员可以授予租户管理员角色"
        elif set(row["permissions"]) - effective_permissions(principal):
            reason = "该角色包含你不能授予的权限"
        result.append({**row, "assignable": not reason, "blocked_reason": reason})
    return {"roles": result, "permissions": PERMISSIONS}


def member_query(repo):
    return (
        repo.query(TenantMember)
        .join(PlatformUser, PlatformUser.user_code == TenantMember.user_code)
        .outerjoin(
            RoleDefinition,
            and_(
                RoleDefinition.role_code == TenantMember.role_code,
                RoleDefinition.tenant_code == TenantMember.tenant_code,
            ),
        )
        .add_columns(
            PlatformUser.username,
            PlatformUser.display_name,
            PlatformUser.status.label("account_status"),
            PlatformUser.platform_admin,
            RoleDefinition.role_name,
            RoleDefinition.status.label("role_status"),
            RoleDefinition.permissions,
        )
    )


def member_view(row, tenant_active):
    member = row[0]
    builtin = next((r for r in builtins() if r["role_code"] == member.role_code), None)
    role_status = builtin["status"] if builtin else row.role_status or 0
    role_name = builtin["role_name"] if builtin else row.role_name or "角色不存在"
    state = "ACTIVE"
    if row.account_status != 1:
        state = "ACCOUNT_DISABLED"
    elif not row.platform_admin and member.status != 1:
        state = "MEMBER_DISABLED"
    elif not row.platform_admin and role_status != 1:
        state = "ROLE_DISABLED"
    principal = Principal(
        member.user_code,
        "",
        platform_admin=row.platform_admin,
        role=member.role_code,
        permissions=tuple(row.permissions or []),
    )
    return {
        **public(member),
        "username": row.username,
        "display_name": row.display_name,
        "account_status": row.account_status,
        "platform_admin": row.platform_admin,
        "role_name": role_name,
        "role_status": role_status,
        "access_state": state,
        "effective_permissions": sorted(effective_permissions(principal))
        if state == "ACTIVE"
        else [],
        "permission_source": "平台管理员" if row.platform_admin else role_name,
        "tenant_readonly": not tenant_active,
    }


async def member_page(repo, principal, search="", status=None, role_code="", limit=50, offset=0):
    query = member_query(repo)
    if search:
        role_name = case(
            (TenantMember.role_code == "TENANT_ADMIN", "租户管理员"),
            (TenantMember.role_code == "MEMBER", "成员"),
            else_=RoleDefinition.role_name,
        )
        query = query.where(
            PlatformUser.username.icontains(search, autoescape=True)
            | PlatformUser.display_name.icontains(search, autoescape=True)
            | TenantMember.user_code.contains(search, autoescape=True)
            | role_name.icontains(search, autoescape=True)
        )
    if status is not None:
        query = query.where(TenantMember.status == status)
    if role_code:
        query = query.where(TenantMember.role_code == role_code)
    rows = (
        await repo.session.execute(
            query.order_by(TenantMember.created_at.desc(), TenantMember.member_code.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [member_view(r, principal.tenant_active) for r in rows]
