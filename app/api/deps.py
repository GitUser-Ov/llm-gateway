"""依赖注入中心。"""
from collections.abc import AsyncIterator
from typing import Annotated
from fastapi import Depends, Header,Request,Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import Settings, get_settings
from app.core.errors import AuthError,RateLimitError
from app.core.security import hash_api_key
from app.db.base import SessionLocal
from app.db.models import User
from app.cache.redis_client import get_redis
from app.core.ratelimit import FixedWindowRateLimiter,RateLimitInfo,DailyQuota

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

async def rate_limit(
    request:Request,
    response:Response,
    user:CurrentUserDep,
    settings:SettingsDep,
)->RateLimitInfo:
    limiter=FixedWindowRateLimiter(
        get_redis(),
        limit=settings.rate_limit_per_minute,
        window_seconds=60,
        namespace="rl:user",
    )
    info=await limiter.check(f"user:{user.id}")

    response.headers["X-RateLimit-Limit"]=str(info.limit)
    response.headers["X-RateLimit-Remaining"]=str(info.remaining)
    response.headers["X-RateLimit-Reset"]=str(info.reset_in)

    if not info.allowed:
        response.headers["Retry-After"]=str(info.reset_in)
        raise RateLimitError(
            f"请求过于频繁，每分钟最多{info.limit}次，请{info.reset_in}秒后重试"
        )
    return info

RateLimitDep=Annotated[RateLimitInfo,Depends(rate_limit)]

async def daily_quota(
        response:Response,
        user:CurrentUserDep,
        settings:SettingsDep,
)->RateLimitInfo:
    quota=DailyQuota(get_redis(),limit=settings.daily_quota,namespace="quota:user")
    info=await quota.consume(f"user:{user.id}")
    response.headers["X-Quota-Limit"]=str(info.limit)
    response.headers["X-Quota-Remaining"]=str(info.remaining)

    if not info.allowed:
        raise RateLimitError(f"今日额度已用完（{info.limit}次），请明天重试")
    return info

QuotaDep=Annotated[RateLimitInfo,Depends(daily_quota)]

