"""直接测试 stream_chat 是否正常出流，定位卡住的原因。"""
from app.llm import client as llm

messages = [
    {"role": "system", "content": "你是校园助手。"},
    {"role": "user", "content": "用 200 字介绍 RAG 的原理"},
]

print("=== 开始迭代 stream_chat ===")
count = 0
try:
    for chunk in llm.stream_chat(messages, temperature=0.7):
        count += 1
        print(f"[{count}] {chunk!r}")
        if count >= 5:
            print("...（已拿到 5 个增量，流正常）")
            break
    print(f"=== 结束，共拿到 {count} 个增量 ===")
except Exception as e:
    print(f"=== 异常 ===")
    print(type(e).__name__, str(e)[:500])
