from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.identity import tenant_context


@event.listens_for(Session, "after_begin")
def set_tenant_scope(session, transaction, connection):
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT set_config('app.tenant_code', :tenant, true)"),
            {"tenant": tenant_context.get()},
        )


settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
