from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderCode(StrEnum):
    DEEPSEEK = "deepseek"
    GLM = "glm"
    MINIMAX = "minimax"
    KIMI = "kimi"


class FunctionCall(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    arguments: str = Field(max_length=1_000_000)


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    type: Literal["function"] = "function"
    function: FunctionCall


class ToolFunction(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str = Field(default="", max_length=4096)
    parameters: dict[str, Any]


class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = Field(default=None, max_length=1_000_000)
    reasoning_content: str | None = Field(default=None, max_length=2_000_000)
    tool_calls: list[ToolCall] | None = Field(default=None, max_length=128)
    tool_call_id: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_role(self) -> Self:
        if self.role != "assistant" and (self.reasoning_content is not None or self.tool_calls):
            raise ValueError("只有 assistant 消息可以包含推理和工具调用")
        if (self.role == "tool") != bool(self.tool_call_id):
            raise ValueError("tool 消息必须携带 tool_call_id，其他角色不能携带")
        if self.role != "assistant" and self.content is None:
            raise ValueError("消息内容不能为空")
        if self.role == "assistant" and not (
            self.content or self.reasoning_content or self.tool_calls
        ):
            raise ValueError("assistant 消息必须包含正文、推理或工具调用")
        return self


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_code: str = Field(min_length=1, max_length=40)
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int | None = Field(default=None, ge=1, le=393_216)
    thinking: Literal["auto", "enabled", "disabled"] = "auto"
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=128)
    tool_choice: Literal["auto", "none", "required"] = "auto"


class ProviderInfo(BaseModel):
    code: ProviderCode
    name: str
    default_model: str
    base_url: str


class ModelResponse(BaseModel):
    message: ChatMessage
    finish_reason: Literal["stop", "length", "tool_calls"]
    provider_request_id: str | None = None
    usage: dict[str, int] | None = None


class ModelStreamEvent(BaseModel):
    type: Literal["start", "reasoning", "content", "usage", "tool_call", "done", "error"]
    delta: str | None = None
    usage: dict[str, int] | None = None
    message: str | None = None
    code: str | None = None
    tool_call: ToolCall | None = None
    response: ModelResponse | None = None
