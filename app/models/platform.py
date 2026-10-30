"""V1 business entities. Public references use immutable codes, never surrogate IDs."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Entity:
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Scoped(Entity):
    tenant_code: Mapped[str] = mapped_column(String(40), index=True)


class Tenant(Entity, Base):
    __tablename__ = "tenant"
    tenant_code: Mapped[str] = mapped_column(String(40), unique=True)
    tenant_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[int] = mapped_column(Integer, default=1)


class PlatformUser(Entity, Base):
    __tablename__ = "platform_user"
    user_code: Mapped[str] = mapped_column(String(40), unique=True)
    external_user_id: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[int] = mapped_column(Integer, default=1)
    username: Mapped[str | None] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class TenantMember(Scoped, Base):
    __tablename__ = "tenant_member"
    __table_args__ = (UniqueConstraint("tenant_code", "user_code"),)
    member_code: Mapped[str] = mapped_column(String(40), unique=True)
    user_code: Mapped[str] = mapped_column(String(40))
    role_code: Mapped[str] = mapped_column(String(32), default="MEMBER")
    status: Mapped[int] = mapped_column(Integer, default=1)


class Agent(Scoped, Base):
    __tablename__ = "agent"
    __table_args__ = (UniqueConstraint("tenant_code", "agent_code"),)
    agent_code: Mapped[str] = mapped_column(String(40), unique=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    agent_type: Mapped[str] = mapped_column(String(32), default="NORMAL")
    current_version_code: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[int] = mapped_column(Integer, default=1)


class AgentVersion(Scoped, Base):
    __tablename__ = "agent_version"
    __table_args__ = (
        UniqueConstraint("tenant_code", "version_code"),
        UniqueConstraint("tenant_code", "agent_code", "version_no"),
        ForeignKeyConstraint(
            ["tenant_code", "profile_code"],
            ["model_profile.tenant_code", "model_profile.profile_code"],
        ),
        ForeignKeyConstraint(
            ["tenant_code", "agent_code"], ["agent.tenant_code", "agent.agent_code"]
        ),
    )
    version_code: Mapped[str] = mapped_column(String(40), unique=True)
    agent_code: Mapped[str] = mapped_column(String(40))
    version_no: Mapped[int] = mapped_column(Integer)
    system_prompt: Mapped[str] = mapped_column(Text)
    profile_code: Mapped[str] = mapped_column(String(40))
    model_snapshot: Mapped[dict] = mapped_column(JSONB)
    model_parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    execution_config: Mapped[dict] = mapped_column(JSONB)
    # Snapshot child version codes and tool definitions at publication, not execution.
    dependency_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="PUBLISHED")


class AgentRelation(Scoped, Base):
    __tablename__ = "agent_relation"
    __table_args__ = (
        UniqueConstraint("tenant_code", "master_agent_code", "sub_agent_code"),
        CheckConstraint("master_agent_code <> sub_agent_code"),
        ForeignKeyConstraint(
            ["tenant_code", "master_agent_code"], ["agent.tenant_code", "agent.agent_code"]
        ),
        ForeignKeyConstraint(
            ["tenant_code", "sub_agent_code"], ["agent.tenant_code", "agent.agent_code"]
        ),
    )
    relation_code: Mapped[str] = mapped_column(String(40), unique=True)
    master_agent_code: Mapped[str] = mapped_column(String(40))
    sub_agent_code: Mapped[str] = mapped_column(String(40))
    call_mode: Mapped[str] = mapped_column(String(32), default="ON_DEMAND")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ToolDefinition(Scoped, Base):
    __tablename__ = "tool_definition"
    __table_args__ = (UniqueConstraint("tenant_code", "tool_code"),)
    tool_code: Mapped[str] = mapped_column(String(40), unique=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    group_name: Mapped[str] = mapped_column(String(64), default="通用")
    tool_type: Mapped[str] = mapped_column(String(32), default="BUILTIN")
    input_schema: Mapped[dict] = mapped_column(JSONB)
    connection_config: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[int] = mapped_column(Integer, default=1)


class AgentToolBinding(Scoped, Base):
    __tablename__ = "agent_tool_binding"
    __table_args__ = (
        UniqueConstraint("tenant_code", "agent_version_code", "tool_code"),
        ForeignKeyConstraint(
            ["tenant_code", "agent_version_code"],
            ["agent_version.tenant_code", "agent_version.version_code"],
        ),
        ForeignKeyConstraint(
            ["tenant_code", "tool_code"],
            ["tool_definition.tenant_code", "tool_definition.tool_code"],
        ),
    )
    binding_code: Mapped[str] = mapped_column(String(40), unique=True)
    agent_version_code: Mapped[str] = mapped_column(String(40))
    tool_code: Mapped[str] = mapped_column(String(40))
    permission_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentTask(Scoped, Base):
    __tablename__ = "agent_task"
    __table_args__ = (
        UniqueConstraint("tenant_code", "task_code"),
        UniqueConstraint("tenant_code", "idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_code", "agent_version_code"],
            ["agent_version.tenant_code", "agent_version.version_code"],
        ),
        Index("idx_task_poll", "status", "scheduled_at", "priority"),
    )
    task_code: Mapped[str] = mapped_column(String(40), unique=True)
    agent_code: Mapped[str] = mapped_column(String(40))
    agent_version_code: Mapped[str] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    input: Mapped[dict] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(40))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))


class AgentRun(Scoped, Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        Index("idx_run_task", "tenant_code", "task_code", "created_at"),
        UniqueConstraint("tenant_code", "run_code"),
        ForeignKeyConstraint(
            ["tenant_code", "task_code"], ["agent_task.tenant_code", "agent_task.task_code"]
        ),
        ForeignKeyConstraint(
            ["tenant_code", "agent_version_code"],
            ["agent_version.tenant_code", "agent_version.version_code"],
        ),
        ForeignKeyConstraint(
            ["tenant_code", "parent_run_code"], ["agent_run.tenant_code", "agent_run.run_code"]
        ),
        ForeignKeyConstraint(
            ["tenant_code", "root_run_code"], ["agent_run.tenant_code", "agent_run.run_code"]
        ),
    )
    run_code: Mapped[str] = mapped_column(String(40), unique=True)
    task_code: Mapped[str] = mapped_column(String(40))
    root_run_code: Mapped[str] = mapped_column(String(40))
    parent_run_code: Mapped[str | None] = mapped_column(String(40))
    parent_step_code: Mapped[str | None] = mapped_column(String(40))
    agent_code: Mapped[str] = mapped_column(String(40))
    agent_version_code: Mapped[str] = mapped_column(String(40))
    lease_token: Mapped[str] = mapped_column(String(40))
    run_depth: Mapped[int] = mapped_column(Integer, default=0)
    turn_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    finish_reason: Mapped[str | None] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunStep(Scoped, Base):
    __tablename__ = "run_step"
    __table_args__ = (
        UniqueConstraint("run_code", "step_no"),
        UniqueConstraint("tenant_code", "step_code"),
        ForeignKeyConstraint(
            ["tenant_code", "run_code"], ["agent_run.tenant_code", "agent_run.run_code"]
        ),
    )
    step_code: Mapped[str] = mapped_column(String(40), unique=True)
    run_code: Mapped[str] = mapped_column(String(40))
    step_no: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    input: Mapped[dict] = mapped_column(JSONB)
    output: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))


class RunMessage(Scoped, Base):
    __tablename__ = "run_message"
    __table_args__ = (
        UniqueConstraint("run_code", "sequence_no"),
        ForeignKeyConstraint(
            ["tenant_code", "run_code"], ["agent_run.tenant_code", "agent_run.run_code"]
        ),
        ForeignKeyConstraint(
            ["tenant_code", "step_code"], ["run_step.tenant_code", "run_step.step_code"]
        ),
    )
    message_code: Mapped[str] = mapped_column(String(40), unique=True)
    run_code: Mapped[str] = mapped_column(String(40))
    step_code: Mapped[str | None] = mapped_column(String(40))
    sequence_no: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class TaskArtifact(Scoped, Base):
    __tablename__ = "task_artifact"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_code", "task_code"], ["agent_task.tenant_code", "agent_task.task_code"]
        ),
        ForeignKeyConstraint(
            ["tenant_code", "run_code"], ["agent_run.tenant_code", "agent_run.run_code"]
        ),
    )
    artifact_code: Mapped[str] = mapped_column(String(40), unique=True)
    task_code: Mapped[str] = mapped_column(String(40))
    run_code: Mapped[str] = mapped_column(String(40))
    artifact_name: Mapped[str] = mapped_column(String(128))
    artifact_type: Mapped[str] = mapped_column(String(32), default="TEXT")
    content: Mapped[dict] = mapped_column(JSONB)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
