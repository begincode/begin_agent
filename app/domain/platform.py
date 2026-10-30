from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TenantInput(Input):
    tenant_name: str = Field(min_length=1, max_length=128)
    status: Literal[0, 1] = 1


class MemberInput(Input):
    external_user_id: str | None = Field(default=None, pattern=r"^100[0-9]{16}$")
    username: str | None = Field(default=None, max_length=32)
    user_code: str | None = Field(default=None, max_length=40)
    display_name: str = Field(min_length=1, max_length=128)
    role_code: str = Field(default="MEMBER", pattern=r"^(TENANT_ADMIN|MEMBER|rol_[0-9A-Z]{26})$")
    status: Literal[0, 1] = 1


class AgentInput(Input):
    agent_name: str = Field(min_length=1, max_length=128)
    agent_type: Literal["NORMAL", "SUPERVISOR", "ROUTER"] = "NORMAL"
    status: Literal[0, 1] = 1


class ExecutionConfig(Input):
    max_steps: int = Field(default=10, ge=1, le=40)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)
    max_sub_agent_calls: int = Field(default=5, ge=0, le=20)
    max_depth: int = Field(default=2, ge=0, le=5)
    max_tokens: int = Field(default=30000, ge=1, le=500000)


class ModelParameters(Input):
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=4096, ge=1, le=65536)
    thinking: Literal["auto", "enabled", "disabled"] = "auto"
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class VersionInput(Input):
    system_prompt: str = Field(min_length=1, max_length=30000)
    profile_code: str = Field(min_length=1, max_length=40)
    model_parameters: ModelParameters = Field(default_factory=ModelParameters)
    execution_config: ExecutionConfig = Field(default_factory=ExecutionConfig)
    tool_codes: list[str] = Field(default_factory=list, max_length=20)
    skill_codes: list[str] = Field(default_factory=list, max_length=10)
    mcp_codes: list[str] = Field(default_factory=list, max_length=10)
    mcp_tool_names: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("tool_codes")
    @classmethod
    def unique_tools(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("工具不能重复绑定")
        return values


class RelationSettings(Input):
    call_mode: Literal["SERIAL", "PARALLEL", "ON_DEMAND"] = "ON_DEMAND"
    timeout_seconds: int = Field(default=120, ge=1, le=1800)
    enabled: bool = True


class RelationInput(RelationSettings):
    sub_agent_code: str = Field(min_length=1, max_length=40)


class ToolInput(Input):
    status: Literal[0, 1] = 1
    tool_name: str = Field(min_length=1, max_length=128)
    group_name: str = Field(default="通用", min_length=1, max_length=64)
    implementation: Literal["text_stats", "utc_now"]


class TaskInput(Input):
    agent_code: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=30000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    priority: int = Field(default=0, ge=-10, le=10)
    max_retries: int = Field(default=2, ge=0, le=5)
