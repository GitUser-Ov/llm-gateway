"""依赖注入中心。"""
from collections.abc import AsyncIterator
from typing import Annotated
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import Settings, get_settings
from app.core.errors import AuthError
from app.core.security import hash_api_key
from app.db.base import SessionLocal
from app.db.models import User

SettingsDep = Annotated[Settings, Depends(get_settings)]

async def get_db() -> AsyncIterator[AsyncSession]:
    """每个请求一个 Session，请求结束自动关闭。"""
    async with SessionLocal() as db:
        try:
            yield db
        except Exception:
            await db.rollback()
            raise

DbDep = Annotated[AsyncSession, Depends(get_db)]

async def get_current_user(
    db: DbDep,
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> User:
    """从 X-API-Key 请求头解析出当前用户。"""
    if not api_key:
        raise AuthError("缺少 X-API-Key 请求头")
    user = await db.scalar(select(User).where(User.api_key_hash == hash_api_key(api_key)))
    if user is None:
        raise AuthError("API Key 无效")
    if not user.is_active:
        raise AuthError("该账号已被禁用")
    return user

CurrentUserDep = Annotated[User, Depends(get_current_user)]