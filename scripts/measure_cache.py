import asyncio
import sys
import time

import httpx

BASE="http://127.0.0.1:8000/api/v1"
QUESTION="这个项目支持哪些大模型上游？"

async def ask(client:httpx.AsyncClient,key:str,question:str,conv_id:int|None=None):
    body={"question":question,"temperature":0.3}
    if conv_id is not None:
        body["conversation_id"]=conv_id
    t0=time.perf_counter()
    r=await client.post(f"{BASE}/chat",json=body,headers={"X-API-Key":key},timeout=60)
    cost=time.perf_counter() - t0
    r.raise_for_status()
    return r.json(),cost

async def main()->None:
    key=sys.argv[1]
    async with httpx.AsyncClient() as client:
        data1,t1=await ask(client,key,QUESTION)
        print(f"第一次问：{t1:6.3f}s from_cache={data1['from_cache']}")

        data2,t2=await ask(client,key,QUESTION,conv_id=data1['conversation_id'])
        print(f"第二次问（同会话）:{t2:6.3f}s from_cache={data2['from_cache']}")

        data3,t3=await ask(client,key,QUESTION)
        print(f"第三次问（新会话）:{t3:6.3f}s from_cache={data3['from_cache']}")

        t0=time.perf_counter()
        results=await asyncio.gather(*[ask(client,key,QUESTION)for _ in range(10)])
        wall=time.perf_counter() - t0
        hits=sum(1 for d,_ in results if d["from_cache"])
        print(f"\n10 个并发问同一问题：总耗时{wall:.3f}s,命中{hits}/10")

        print(f"\n加速倍数:{t1/max(t3,0.001):.0f}x")

if __name__ == "__main__":
    asyncio.run(main())


