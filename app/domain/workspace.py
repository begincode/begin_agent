from typing import Literal

from pydantic import Field, SecretStr

from app.domain.platform import Input, VersionInput


class DraftInput(VersionInput):
    profile_code: str = Field(default="", max_length=40)
    revision: int = Field(default=0, ge=0)


class TurnInput(Input):
    prompt: str = Field(min_length=1, max_length=30000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class RerunInput(Input):
    source_turn_code: str = Field(min_length=1, max_length=40)
    idempotency_key: str = Field(min_length=1, max_length=128)


class DebugInput(TurnInput):
    version_code: str | None = Field(default=None, max_length=40)
    use_draft: bool = False


class RollbackInput(Input):
    version_code: str = Field(min_length=1, max_length=40)


class McpInput(Input):
    mcp_name: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=1, max_length=512)
    token: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    clear_token: bool = False
    status: Literal[0, 1] = 1


class SkillInput(Input):
    skill_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    instructions: str = Field(min_length=1, max_length=20000)
    status: Literal[0, 1] = 1


class RoleInput(Input):
    role_name: str = Field(min_length=1, max_length=128)
    permissions: list[str] = Field(max_length=30)
    status: Literal[0, 1] = 1


class GrantInput(Input):
    resource_kind: Literal["MODEL", "TOOL", "MCP", "SKILL"]
    resource_code: str = Field(min_length=1, max_length=40)
    enabled: bool


class TestCaseInput(Input):
    case_name: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=30000)
    expected_contains: str = Field(min_length=1, max_length=2000)
    status: Literal[0, 1] = 1


class UserInput(Input):
    platform_admin: bool | None = None
    display_name: str = Field(min_length=1, max_length=128)
    status: Literal[0, 1] = 1


class TenantNameInput(Input):
    tenant_name: str = Field(min_length=1, max_length=128)
