"""Verified account sessions establish identity; tenant headers only select membership."""

import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass

LOCAL_TENANT = "ten_00000000000000000000000000"
LOCAL_USER = "usr_00000000000000000000000000"
tenant_context: ContextVar[str] = ContextVar("tenant_code", default=LOCAL_TENANT)


def new_code(prefix: str) -> str:
    number = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    return prefix + "_" + "".join(alphabet[(number >> n) & 31] for n in range(125, -1, -5))


@dataclass(frozen=True)
class Principal:
    user_code: str
    external_user_id: str
    platform_admin: bool = False
    tenant_code: str = LOCAL_TENANT
    role: str = "MEMBER"
    tenant_active: bool = True
    permissions: tuple[str, ...] = ()
