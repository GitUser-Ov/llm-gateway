import logging
import time
from dataclasses import dataclass
import redis.asyncio as aioredis

logger=logging.getLogger(__name__)

@dataclass
class RateLimitInfo:
    allowed:bool
    limit:int
    remaining:int
    reset_in:int
    used:int

class FixedWindowRateLimiter:
    def __init__(
            self,
            redis:aioredis.Redis,
            limit:int,
            window_seconds:int=60,
            namespace:str="rl",
    )->None:
        self._redis=redis
        self._limit=max(1,limit)
        self._window=max(1,window_seconds)
        self._ns=namespace

    def _key(self,identity:str,now:float)->tuple[str,int]:
        window_id=int(now//self._window)
        return f"{self._ns}:{self._window}s:{identity}:{window_id}",window_id

    async def check(self,identity:str)->RateLimitInfo:
        now=time.time()
        key,window_id=self._key(identity,now)
        reset_in=int(self._window-(now%self._window))or 1
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key,self._window+1)
                results=await pipe.execute()
            used=int(results[0])
        except Exception:
            logger.warning("限流器故障，本次请求放行",exc_info=True)
            return RateLimitInfo(True,self._limit,self._limit,reset_in,0)

        allowed=used<=self._limit
        return RateLimitInfo(
            allowed=allowed,
            limit=self._limit,
            remaining=max(0,self._limit-used),
            reset_in=reset_in,
            used=used,
        )

class DailyQuota:
    def __init__(self,redis:aioredis.Redis,limit:int,namespace:str="quota")->None:
        self._redis=redis
        self._limit=max(1,limit)
        self._ns=namespace

    async def consume(self,identity:str)->RateLimitInfo:
        now=time.time()
        day=time.strftime("%Y%m%d",time.localtime(now))
        key=f"{self._ns}:{identity}:{day}"
        tomorrow=time.mktime(time.strptime(time.strftime("%Y%m%d",time.localtime(now+86400)),"%Y%m%d"))
        reset_in=max(1,int(tomorrow-now))

        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key,reset_in+60)
                results=await pipe.execute()
            used=int(results[0])

        except Exception:
            logger.warning("配额检查失败，放行",exc_info=True)
            return RateLimitInfo(True,self._limit,self._limit,reset_in,0)

        allowed=used<=self._limit
        return RateLimitInfo(allowed,self._limit,max(0,self._limit-used),reset_in,used)
    
        