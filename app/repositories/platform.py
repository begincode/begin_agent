from collections.abc import AsyncIterator

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import tenant_context
from app.db.session import session_factory


async def database_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


class TenantRepository:
    def __init__(self, session: AsyncSession, tenant_code: str | None = None):
        self.session = session
        self.tenant_code = tenant_code if tenant_code is not None else tenant_context.get()

    def query(self, model):
        return select(model).where(model.tenant_code == self.tenant_code)

    async def get(self, model, field, code, lock=False):
        statement = self.query(model).where(getattr(model, field) == code)
        if lock:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement)
        if row is None:
            raise HTTPException(404, "记录不存在")
        return row

    async def list(self, model, limit=50, offset=0, **filters):
        query = self.query(model)
        for key, value in filters.items():
            query = query.where(getattr(model, key) == value)
        return (
            await self.session.scalars(
                query.order_by(model.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()


def public(row):
    return {
        column.key: getattr(row, column.key)
        for column in row.__table__.columns
        if column.key not in {"id", "lease_token", "metadata", "password_hash"}
        and not column.key.endswith("ciphertext")
    }
