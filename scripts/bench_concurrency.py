import asyncio
import time

import httpx

BASE="http://127.0.0.1:8000/api/v1/_bench"
CONCURRENCY=60

async def one(client:httpx.AsyncClient,url:str)->float:
    t0=time.perf_counter()
    r=await client.get(url,timeout=60)
    r.raise_for_status()
    return time.perf_counter() - t0

async def run(kind:str)->tuple[float,float,float]:
    url=f"{BASE}/{kind}-sleep"
    async with httpx.AsyncClient() as client:
        t0=time.perf_counter()
        costs=await asyncio.gather(*[one(client,url) for _ in range(CONCURRENCY)])
        wall=time.perf_counter() -t0
    return wall,min(costs),max(costs)

async def main()->None:
    print(f"并发{CONCURRENCY}个请求，每个接口内部sleep1.5秒")
    print("-"*62)

    for kind in ("sync","async"):
        wall,fastest,slowest=await run(kind)
        print(
            f"{kind:<6}路由 总耗时{wall:6.2f}s  "
            f"单请求耗时{fastest:.2f}s~{slowest:.2f}s"
            )

    print("-"*62)
    print("怎么看结果：")
    print(" 总耗时=所有请求跑完的墙钟时间（越小越好）")
    print(" 单请求耗时区间=每个请求自己等了多久（越均匀越好）")

if __name__=="__main__":
    asyncio.run(main())
