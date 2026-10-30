"""Persistent configuration and task conversation extensions."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.platform import Scoped


class AgentDraft(Scoped, Base):
    __tablename__ = "agent_draft"
    __table_args__ = (UniqueConstraint("tenant_code", "agent_code"),)
    draft_code: Mapped[str] = mapped_column(String(40), unique=True)
    agent_code: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TaskTurn(Scoped, Base):
    __tablename__ = "task_turn"
    __table_args__ = (
        UniqueConstraint("tenant_code", "task_code", "turn_no"),
        UniqueConstraint("tenant_code", "task_code", "idempotency_key"),
    )
    turn_code: Mapped[str] = mapped_column(String(40), unique=True)
    task_code: Mapped[str] = mapped_column(String(40))
    turn_no: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    output: Mapped[dict | None] = mapped_column(JSONB)
    run_code: Mapped[str | None] = mapped_column(String(40))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpConnection(Scoped, Base):
    __tablename__ = "mcp_connection"
    mcp_code: Mapped[str] = mapped_column(String(40), unique=True)
    mcp_name: Mapped[str] = mapped_column(String(128))
    endpoint: Mapped[str] = mapped_column(String(512))
    token_ciphertext: Mapped[str | None] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer, default=1)
    tools: Mapped[list] = mapped_column(JSONB, default=list)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(128))


class SkillDefinition(Scoped, Base):
    __tablename__ = "skill_definition"
    skill_code: Mapped[str] = mapped_column(String(40), unique=True)
    skill_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    instructions: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer, default=1)


class ResourceGrant(Scoped, Base):
    __tablename__ = "resource_grant"
    __table_args__ = (UniqueConstraint("tenant_code", "resource_kind", "resource_code"),)
    grant_code: Mapped[str] = mapped_column(String(40), unique=True)
    resource_kind: Mapped[str] = mapped_column(String(16))
    resource_code: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class RoleDefinition(Scoped, Base):
    __tablename__ = "role_definition"
    __table_args__ = (UniqueConstraint("tenant_code", "role_code"),)
    role_code: Mapped[str] = mapped_column(String(40), unique=True)
    role_name: Mapped[str] = mapped_column(String(128))
    permissions: Mapped[list] = mapped_column(JSONB)
    status: Mapped[int] = mapped_column(Integer, default=1)


class AgentTestCase(Scoped, Base):
    __tablename__ = "agent_test_case"
    case_code: Mapped[str] = mapped_column(String(40), unique=True)
    agent_code: Mapped[str] = mapped_column(String(40))
    case_name: Mapped[str] = mapped_column(String(128))
    prompt: Mapped[str] = mapped_column(Text)
    expected_contains: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer, default=1)


class AuditEvent(Scoped, Base):
    __tablename__ = "audit_event"
    event_code: Mapped[str] = mapped_column(String(40), unique=True)
    actor_code: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(128))
    resource_code: Mapped[str] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class AccountSession(Base):
    __tablename__ = "account_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_code: Mapped[str] = mapped_column(String(40), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginBucket(Base):
    __tablename__ = "login_bucket"
    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
