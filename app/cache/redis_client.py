import logging

import redis.asyncio as aioredis

from app.core.config import get_settings

logger=logging.getLogger(__name__)
settings=get_settings()

_client:aioredis.Redis|None=None

def get_redis()->aioredis.Redis:
    global _client
    if _client is None:
        _client=aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
            socket_timeout=2,
            socket_connect_timeout=2,
        )

    return _client

async def close_redis()->None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client=None
