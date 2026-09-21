from fastapi import FastAPI

from app.api.v1.router import api_router

from app.core.config import get_settings

from app.core.handlers import register_exception_handlers

settings=get_settings()

app=FastAPI(
    title="Campus AI服务",
    version="0.1.0",
    description="校园知识助手---后端服务（进阶一）",
)

register_exception_handlers(app)

app.include_router(api_router)


