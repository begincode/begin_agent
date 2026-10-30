"""Shared API authorization; role labels are not permissions."""

from fastapi import HTTPException

PERMISSIONS = {
    "tasks.read": "查看任务",
    "tasks.create": "发起任务与对话",
    "tasks.manage": "管理全部任务",
    "agents.read": "查看 Agent",
    "agents.manage": "配置 Agent",
    "agents.publish": "发布及回滚版本",
    "runs.read": "查看运行记录",
    "resources.read": "查看资源",
    "resources.manage": "配置资源",
    "members.manage": "管理成员",
    "roles.manage": "管理角色权限",
    "tenant.manage": "租户设置与授权",
    "audit.read": "查看审计记录",
}
MEMBER_PERMISSIONS = {"tasks.read", "tasks.create", "agents.read", "runs.read", "resources.read"}


def effective_permissions(principal):
    if principal.platform_admin or principal.role == "TENANT_ADMIN":
        return set(PERMISSIONS)
    if principal.role == "MEMBER":
        return set(MEMBER_PERMISSIONS)
    return set(principal.permissions)


def allowed(principal, permission):
    return permission in effective_permissions(principal)


def require(principal, permission):
    alternatives = permission if isinstance(permission, tuple) else (permission,)
    if not any(allowed(principal, item) for item in alternatives):
        raise HTTPException(403, "没有此操作权限")


def request_permission(method, path):
    parts = path.removeprefix("/api/v1/").split("/")
    kind = parts[0]
    read = method in {"GET", "HEAD", "OPTIONS"}
    if kind in {"me", "auth", "overview", "integration-status"}:
        return None
    if kind == "agents":
        if read:
            return "agents.read"
        if "versions" in parts or "rollback" in parts:
            return "agents.publish"
        if "debug" in parts or "run" in parts:
            return "tasks.create"
        return "agents.manage"
    if kind == "tasks":
        if not read and len(parts) == 3 and parts[2] in {"cancel", "retry", "rerun"}:
            return ("tasks.create", "tasks.manage")
        return "tasks.read" if read else "tasks.create"
    if kind == "runs":
        return "runs.read"
    if kind in {"models", "model-profiles", "tools", "mcps", "skills"}:
        return "resources.read" if read else "resources.manage"
    if kind == "chat":
        return "tasks.create"
    if kind == "members":
        return "members.manage"
    if kind == "tenant":
        return "tenant.manage"
    if kind == "roles":
        return "roles.manage"
    if kind == "resources":
        return "resources.read" if read else "tenant.manage"
    if kind == "audit":
        return "audit.read"
    return None
