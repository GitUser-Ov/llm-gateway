from fastapi import APIRouter

from app.core.config import get_settings

router=APIRouter(tags=["健康检查"])

@router.get("/health",summary="服务健康检查")

def health():
    settings=get_settings()
    return{
    "status":"ok",
    "app":settings.app_name,
    "env":settings.env,
    }

