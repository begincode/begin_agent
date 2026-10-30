import json
import logging
import secrets
from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.credentials import CredentialCipher
from app.db.session import session_factory
from app.model_gateway.domain.chat import (
    ChatCompletionRequest,
    ChatMessage,
    ModelResponse,
    ModelStreamEvent,
    ProviderInfo,
)
from app.model_gateway.errors import (
    InvalidModelRequest,
    ProviderNotConfiguredError,
    ProviderRequestError,
)
from app.model_gateway.gateway import ModelGateway
from app.services.model_profiles import (
    ModelProfileService,
    ProfileCreate,
    ProfileInfo,
    ProfileUpdate,
)

logger = logging.getLogger(__name__)
chat_router = APIRouter(prefix="/api/v1", tags=["models"])


@lru_cache
def get_model_gateway() -> ModelGateway:
    return ModelGateway(get_settings())


async def profile_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


def profile_service(session: AsyncSession) -> ModelProfileService:
    return ModelProfileService(session, CredentialCipher(get_settings()))


def require_model_admin(request: Request) -> None:
    settings = get_settings()
    if settings.auth_mode != "local":
        from app.api.auth import current_principal

        p = current_principal(request)
        from app.services.permissions import allowed

        if p.tenant_code and allowed(p, "resources.manage"):
            return
        raise HTTPException(403, "模型配置需要租户管理员权限")
    if settings.model_admin_token is not None:
        expected = f"Bearer {settings.model_admin_token.get_secret_value()}"
        if secrets.compare_digest(request.headers.get("authorization", ""), expected):
            return
    elif (
        settings.environment == "local"
        and request.client
        and request.client.host
        in {
            "127.0.0.1",
            "::1",
        }
    ):
        return
    raise HTTPException(403, "模型配置需要管理员权限")


def encode_sse(event: ModelStreamEvent) -> str:
    payload = event.model_dump(exclude_none=True)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@chat_router.get("/models", response_model=list[ProviderInfo])
async def list_models() -> list[ProviderInfo]:
    return get_model_gateway().registry.list()


@chat_router.get("/model-profiles", response_model=list[ProfileInfo])
async def list_profiles(session: AsyncSession = Depends(profile_session)) -> list[ProfileInfo]:
    return await profile_service(session).list()


@chat_router.get("/model-profiles/{profile_code}", response_model=ProfileInfo)
async def profile_detail(profile_code: str, session: AsyncSession = Depends(profile_session)):
    try:
        return ProfileInfo.model_validate(await profile_service(session)._get(profile_code))
    except ProviderNotConfiguredError:
        raise HTTPException(404, "模型配置不存在") from None


@chat_router.post(
    "/model-profiles",
    response_model=ProfileInfo,
    status_code=201,
    dependencies=[Depends(require_model_admin)],
)
async def create_profile(
    data: ProfileCreate, session: AsyncSession = Depends(profile_session)
) -> ProfileInfo:
    return await profile_service(session).create(data)


@chat_router.patch(
    "/model-profiles/{profile_code}",
    response_model=ProfileInfo,
    dependencies=[Depends(require_model_admin)],
)
async def update_profile(
    profile_code: str, data: ProfileUpdate, session: AsyncSession = Depends(profile_session)
) -> ProfileInfo:
    return await profile_service(session).update(profile_code, data)


async def resolve_provider(request: ChatCompletionRequest):
    # Close the read transaction before opening the long-running provider connection.
    async with session_factory() as session:
        return await profile_service(session).resolve(request.profile_code)


@chat_router.post("/chat", response_model=ModelResponse)
async def generate_chat(request: ChatCompletionRequest) -> ModelResponse:
    provider = await resolve_provider(request)
    return await get_model_gateway().generate(request, provider)


@chat_router.post("/chat/stream")
async def stream_chat(request: ChatCompletionRequest) -> StreamingResponse:
    provider = await resolve_provider(request)
    gateway = get_model_gateway()
    gateway.validate(request, provider)

    async def events() -> AsyncIterator[str]:
        upstream = gateway.stream(request, provider)
        try:
            async for event in upstream:
                yield encode_sse(event)
        except ProviderRequestError as exc:
            logger.warning(
                "Model request failed: provider=%s status=%s code=%s",
                provider.code,
                exc.status_code,
                exc.code,
            )
            yield encode_sse(ModelStreamEvent(type="error", message=str(exc), code=exc.code))
        except Exception:
            # Don't log raw upstream messages, request bodies or key-bearing exception strings.
            logger.error("Unexpected model gateway error: provider=%s", provider.code)
            yield encode_sse(
                ModelStreamEvent(type="error", message="模型服务调用失败", code="INTERNAL_ERROR")
            )
        finally:
            await upstream.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_router.post(
    "/model-profiles/{profile_code}/verify", dependencies=[Depends(require_model_admin)]
)
async def verify_profile(profile_code: str) -> dict:
    request = ChatCompletionRequest(
        profile_code=profile_code,
        messages=[ChatMessage(role="user", content="只回复 OK")],
        max_tokens=2048,
    )
    provider = await resolve_provider(request)
    response = await get_model_gateway().generate(request, provider)
    passed = response.finish_reason == "stop" and bool(response.message.content)
    return {
        "passed": passed,
        "scope": "single_text_request",
        "finish_reason": response.finish_reason,
        "usage": response.usage,
        "provider_request_id": response.provider_request_id,
    }


def install_model_error_handlers(application) -> None:
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    @application.exception_handler(IntegrityError)
    async def conflict(_request, _exc):
        return JSONResponse(status_code=409, content={"detail": "记录冲突，请刷新后重试"})

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        # Pydantic errors normally echo input. Never echo submitted API keys or messages.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": error["loc"], "type": error["type"], "msg": "字段格式或取值不合法"}
                    for error in exc.errors()
                ]
            },
        )

    @application.exception_handler(ProviderNotConfiguredError)
    async def missing_profile(_request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @application.exception_handler(ProviderRequestError)
    async def provider_error(_request, exc):
        status = 422 if isinstance(exc, InvalidModelRequest) else 502
        return JSONResponse(status_code=status, content={"detail": str(exc), "code": exc.code})

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_request, _exc):
        return JSONResponse(status_code=503, content={"detail": "数据库不可用，请检查连接与迁移"})
