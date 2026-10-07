"""知识库检索质量评测：裸模型 vs RAG。

这是整个 RAG 改造里最重要的一步——没有评测，就无法回答
「加了检索到底有没有用」这个唯一重要的问题。

用法：
    python -m scripts.eval_rag            # 只测检索准确率（快，不花钱）
    python -m scripts.eval_rag --full     # 连模型答案一起测（会消耗 token）
"""
import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.db.base import SessionLocal
from app.llm import client as llm
from app.llm.prompts import RAG_SYSTEM_PROMPT, SYSTEM_PROMPT, build_rag_user_message
from app.rag.service import search

# (问题, 期望命中的关键词) —— 关键词必须出现在正确片段里
CASES = [
    ("这个项目支持哪些大模型上游？", ["LLM_UPSTREAMS", "上游"]),
    ("缓存命中能把响应时间降到多少？", ["0.016", "123"]),
    ("主上游挂了会怎样？", ["故障转移", "备用"]),
    ("限流和配额是怎么实现的？", ["固定窗口", "配额"]),
    ("流式输出为什么不能重试？", ["重试", "重复"]),
    ("怎么把文档灌进知识库？", ["ingest", "切片"]),
    ("问答接口需要什么认证？", ["X-API-Key"]),
    ("token 用量记录在哪里？", ["prompt_tokens", "completion_tokens"]),
    ("数据库迁移用什么工具？", ["alembic", "Alembic"]),
    ("怎么启动整个项目？", ["docker", "compose"]),
    ("向量模型用的哪个？", ["bge", "embed"]),
    ("为什么降级结果不写缓存？", ["缓存", "降级"]),
]


def hit_expected(chunks, keywords: list[str]) -> bool:
    if not chunks:
        return False
    blob = " ".join(c.content for c in chunks)
    return any(k.lower() in blob.lower() for k in keywords)


async def eval_retrieval() -> tuple[int, int]:
    print("=" * 64)
    print("第一部分：检索准确率（top-k 里是否出现了正确依据）")
    print("=" * 64)
    hits = 0
    async with SessionLocal() as db:
        for i, (q, keywords) in enumerate(CASES, 1):
            chunks = await search(db, q)
            ok = hit_expected(chunks, keywords)
            hits += ok
            top = f"{chunks[0].score:.3f}" if chunks else "-"
            src = chunks[0].citation if chunks else "（无）"
            print(f"  {'✓' if ok else '✗'} [{i:2d}] {q}")
            print(f"        命中 {len(chunks)} 条  最高分 {top}  来源 {src}")
    print(f"\n检索命中率: {hits}/{len(CASES)} = {hits / len(CASES):.0%}")
    return hits, len(CASES)


async def eval_answers() -> None:
    settings = get_settings()
    print()
    print("=" * 64)
    print("第二部分：回答质量对比（裸模型 vs 带检索）")
    print("=" * 64)
    print(f"上游: {settings.upstream_list[0].name} / {settings.primary_model}")
    print("注意：本部分会真实调用模型，消耗 token\n")

    # 只挑 3 题做对比，避免评测本身花太多钱
    probe = CASES[:3]
    naive_ok = rag_ok = 0

    async with SessionLocal() as db:
        for q, keywords in probe:
            # 裸模型
            naive_msgs = [{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": q}]
            try:
                naive = await llm.chat(naive_msgs, temperature=0.3)
            except Exception as exc:
                naive = f"<调用失败: {exc}>"
            n_ok = any(k.lower() in naive.lower() for k in keywords)
            naive_ok += n_ok

            # 带检索
            chunks = await search(db, q)
            rag_msgs = [
                {"role": "system", "content": RAG_SYSTEM_PROMPT},
                {"role": "user", "content": build_rag_user_message(
                    q, [c.content for c in chunks]
                )},
            ]
            try:
                rag_ans = await llm.chat(rag_msgs, temperature=0.3)
            except Exception as exc:
                rag_ans = f"<调用失败: {exc}>"
            r_ok = any(k.lower() in rag_ans.lower() for k in keywords)
            rag_ok += r_ok

            print(f"问题：{q}")
            print(f"  期望包含：{keywords}")
            print(f"  裸模型 {'✓' if n_ok else '✗'}：{naive[:100].replace(chr(10), ' ')}")
            print(f"  RAG   {'✓' if r_ok else '✗'}：{rag_ans[:100].replace(chr(10), ' ')}")
            print()

    n = len(probe)
    print(f"裸模型命中: {naive_ok}/{n} = {naive_ok / n:.0%}")
    print(f"RAG  命中: {rag_ok}/{n} = {rag_ok / n:.0%}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="同时评测回答质量（消耗 token）")
    args = parser.parse_args()

    await eval_retrieval()
    if args.full:
        await eval_answers()
    else:
        print("\n（加 --full 可继续评测回答质量，会消耗 token）")


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
