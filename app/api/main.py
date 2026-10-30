from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.api.accounts import accounts_router
from app.api.auth import IdentityMiddleware, auth_router
from app.api.chat import install_model_error_handlers
from app.api.platform import platform_router
from app.api.router import router
from app.api.workspace import workspace_router
from app.core.config import get_settings
from app.core.directories import prepare_directories
from app.core.logging import configure_logging
from app.db.session import session_factory

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def mount_frontend(application: FastAPI) -> None:
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        return

    static_root = STATIC_DIR.resolve()

    @application.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str, request: Request):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)

        requested_file = (static_root / full_path).resolve()
        if requested_file.is_relative_to(static_root) and requested_file.is_file():
            return FileResponse(requested_file)
        from html import escape
        from urllib.parse import urlencode

        from fastapi.responses import HTMLResponse, RedirectResponse

        from app.api.auth import COOKIE

        prefix = request.scope.get("root_path", "").rstrip("/")
        if get_settings().auth_mode == "sso" and not request.cookies.get(COOKIE):
            return RedirectResponse(
                prefix + "/sso/login?" + urlencode({"returnTo": prefix + "/"}), status_code=303
            )
        html = index_file.read_text().replace(
            "<head>", '<head><base href="' + escape(prefix + "/", quote=True) + '">'
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def create_app() -> FastAPI:
    settings = get_settings()
    prepare_directories(settings)
    configure_logging(settings.log_level, settings.log_dir)

    @asynccontextmanager
    async def lifespan(_application):
        if settings.auth_mode != "local" and settings.environment not in {"local", "test"}:
            async with session_factory() as session:
                privileged = await session.scalar(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
                if privileged:
                    raise RuntimeError("API 数据库角色必须为非超级用户且 NOBYPASSRLS")
        yield

    application = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        docs_url="/docs" if settings.environment in {"local", "test"} else None,
        redoc_url=None,
    )
    application.include_router(router)
    application.include_router(auth_router)
    application.include_router(platform_router)
    application.include_router(workspace_router)
    application.include_router(accounts_router)
    application.add_middleware(IdentityMiddleware)
    install_model_error_handlers(application)
    mount_frontend(application)
    return application


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.environment == "local",
        access_log=False,
    )


if __name__ == "__main__":
    run()
