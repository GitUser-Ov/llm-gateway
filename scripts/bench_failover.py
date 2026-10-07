"""多上游故障转移基准：主上游不可用时，请求是否仍然成功。

用法：
    python scripts/bench_failover.py <API_KEY>

前提：.env 里配了至少两个上游，第一个故意指向不可用地址（模拟上游宕机）。
这个脚本回答的问题是「上游挂了，用户会怎样」——
在网关改造之前答案是 502；改造之后答案是「无感知，只是慢了一点」。
"""
import asyncio
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
QUESTION = "缓考怎么申请的？"


async def one(client: httpx.AsyncClient, key: str) -> tuple[bool, float, dict]:
    body = {"question": QUESTION, "temperature": 0.3}
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE}/chat", json=body,
            headers={"X-API-Key": key},
            timeout=90,
        )
        cost = time.perf_counter() - t0
        if r.status_code != 200:
            return False, cost, {"status": r.status_code}
        return True, cost, r.json()
    except Exception as exc:
        return False, time.perf_counter() - t0, {"error": repr(exc)}


async def main() -> None:
    key = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    async with httpx.AsyncClient() as client:
        print(f"发起 {n} 次请求（主上游已宕机，应由备用上游接管）")
        print("-" * 62)
        ok = 0
        costs: list[float] = []
        for i in range(n):
            success, cost, data = await one(client, key)
            costs.append(cost)
            ok += success
            tag = "成功" if success else "失败"
            upstream = data.get("upstream", "-")
            print(
                f"  第{i + 1}次: {tag}  耗时{cost:6.2f}s  "
                f"实际服务上游={upstream}  model={data.get('model', '-')}"
            )
        print("-" * 62)
        print(f"成功率: {ok}/{n}")
        print(f"平均耗时: {sum(costs) / len(costs):.2f}s  "
              f"最快{min(costs):.2f}s  最慢{max(costs):.2f}s")
        print()
        print("怎么看结果：")
        print("  成功率 100% = 故障转移生效，上游宕机对用户无感")
        print("  耗时含主上游的重试退避，这是『不轻易放弃一个上游』的代价")


if __name__ == "__main__":
    asyncio.run(main())
