"""用 python 直接请求流式端点，测试 HTTP 层是否正常出流。"""
import httpx

url = "http://127.0.0.1:8000/api/v1/chat/stream"
headers = {
    "X-API-Key": "sk-campus-KXUaXoinFG1x6vKDkX4hOqrfykvq0m7M",
    "Content-Type": "application/json",
}
body = {"question": "用 200 字介绍 RAG 的原理", "temperature": 0.7}

print("=== 开始请求流式端点 ===")
count = 0
try:
    with httpx.stream("POST", url, headers=headers, json=body, timeout=30) as resp:
        print("HTTP 状态码:", resp.status_code)
        print("响应头 media_type:", resp.headers.get("content-type"))
        for line in resp.iter_lines():
            count += 1
            print(f"[{count}] {line}")
            if count >= 6:
                break
    print("=== 完成，前几条已拿到 ===")
except Exception as e:
    print("=== 异常 ===")
    print(type(e).__name__, str(e)[:500])
