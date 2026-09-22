"""依赖注入中心。"""
from collections.abc import Iterator
from typing import Annotated
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import Settings, get_settings
from app.core.errors import AuthError
from app.core.security import hash_api_key
from app.db.base import SessionLocal
from app.db.models import User

SettingsDep = Annotated[Settings, Depends(get_settings)]

def get_db() -> Iterator[Session]:
    """每个请求一个 Session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

DbDep = Annotated[Session, Depends(get_db)]

def get_current_user(
    db: DbDep,
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> User:
    """从 X-API-Key 请求头解析出当前用户。"""
    if not api_key:
        raise AuthError("缺少 X-API-Key 请求头")
    user = db.scalar(select(User).where(User.api_key_hash == hash_api_key(api_key)))
    if user is None:
        raise AuthError("API Key 无效")
    if not user.is_active:
        raise AuthError("该账号已被禁用")
    return user

CurrentUserDep = Annotated[User, Depends(get_current_user)]