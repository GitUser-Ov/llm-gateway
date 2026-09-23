from fastapi import FastAPI

from app.api.v1.router import api_router

from app.core.config import get_settings

from app.core.handlers import register_exception_handlers

from app.core.logging import setup_logging

from app.core.middleware import RequestContextMiddleware

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app.core.config import BASE_DIR



settings=get_settings()

setup_logging(level=settings.log_level,json_output=settings.is_prod)


app=FastAPI(
    title="Campus AI服务",
    version="0.1.0",
    description="校园知识助手---后端服务（进阶一）",
)

app.add_middleware(RequestContextMiddleware)
register_exception_handlers(app)
app.include_router(api_router)
WEB_DIR=Path(BASE_DIR)/"web"
if WEB_DIR.exists():
    app.mount("/static",StaticFiles(directory=str(WEB_DIR)),name="static")


