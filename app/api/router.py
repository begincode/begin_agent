from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.chat import chat_router
from app.db.session import session_factory

router = APIRouter()
router.include_router(chat_router)


@router.get("/healthz", tags=["system"])
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["system"])
async def readycheck() -> dict[str, str]:
    try:
        async with session_factory() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "20261029_06":
            raise HTTPException(503, "数据库迁移未就绪")
    except SQLAlchemyError as exc:
        raise HTTPException(503, "数据库未就绪") from exc
    return {"status": "ready"}
