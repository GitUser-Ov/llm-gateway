import hashlib
import json
import logging
import random

import redis.asyncio as aioredis

from app.core.config import get_settings

logger=logging.getLogger(__name__)

settings=get_settings()

KEY_PREFIX="ans:v1"

def make_key(messages:list[dict],model:str,temperature:float)->str:
    payload=json.dumps(
        {
            "messages":messages,
            "model":model,
            "temperature":round(float(temperature),2),
        },
        ensure_ascii=False,
        sort_keys=True,
        )

    digest=hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"{KEY_PREFIX}:{digest}"

def _ttl_with_jitter()->int:
    return max(60,settings.answer_cache_ttl+random.randint(-300,300))

async def get_answer(
    r:aioredis.Redis,messages:list[dict],model:str,temperature:float
    )-> str|None:
    key=make_key(messages,model,temperature)
    try:
        value=await r.get(key)
    except Exception:
        logger.warning("读取缓存失败，按未命中处理",exc_info=True)
        return None
    if value is None:
        logger.info("缓存未命中",extra={"extra_fields":{"cache":"miss"}})
        return None

    logger.info("缓存命中",extra={"extra_fields":{"cache":"hit"}})
    return value

async def set_answer(
    r:aioredis.Redis,messages:list[dict],model:str,temperature:float,answer:str
    )->None:
    key=make_key(messages,model,temperature)
    ttl=_ttl_with_jitter()
    try:
        await r.set(key,answer,ex=ttl)
        logger.info("写入缓存",extra={"extra_fields":{"cache":"set","ttl":ttl}})
    except Exception:
        logger.warning("写入缓存失败",exc_info=True)
