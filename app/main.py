import asyncio
import sys

if sys.platform=="win32":
    def win_loop_factory(use_subprocess:bool=False):
        return asyncio.SelectorEventLoop()


from fastapi import FastAPI

from app.api.v1.router import api_router

from app.core.config import BASE_DIR,get_settings

from app.core.handlers import register_exception_handlers

from app.core.logging import setup_logging

from app.core.middleware import RequestContextMiddleware

from app.db.base import engine

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager

from fastapi.responses import FileResponse

from fastapi.middleware.cors import CORSMiddleware

settings=get_settings()

setup_logging(level=settings.log_level,json_output=settings.is_prod)

@asynccontextmanager
async def lifespan(app:FastAPI):
    setup_logging(level=settings.log_level,json_output=settings.is_prod)
    yield
    await engine.dispose()

app=FastAPI(
    title="Campus AI · 校园问答服务",
    version="0.1.0",
    description=(
        "校园问答大模型服务后端：会话记忆、SSE 流式输出、"
        "分钟级限流与按天配额、精确缓存、模型故障降级。"
    ),
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
register_exception_handlers(app)
app.include_router(api_router)

WEB_DIR=Path(BASE_DIR)/"web"
if WEB_DIR.exists():
    app.mount("/static",StaticFiles(directory=str(WEB_DIR)),name="static")

    @app.get("/",include_in_schema=False)
    async def index()->FileResponse:
        return FileResponse(str(WEB_DIR/"index.html"))

if settings.cors_origin_list!=["*"] or not settings.is_prod:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID","X-RateLimit-Remaining","Retry-After"],
    )

    



