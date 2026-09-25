import pytest
from app.core.config import get_settings

async def test_rate_limit_returns_429(client,auth_headers,monkeypatch,fake_llm):
    settings=get_settings()
    monkeypatch.setattr(settings,"rate_limit_per_minute",2,raising=False)

    payload={"question":"限流测试","temperature":0.3}
    codes=[]
    for i in range(4):
        r=await client.post(
            "/api/v1/chat",json={**payload,"question":f"限流测试{i}"},headers=auth_headers
        )

        codes.append(r.status_code)

    assert codes[0]==200
    assert codes[1]==200
    assert codes[2]==429
    assert codes[3]==429

async def test_rate_limit_headers_present(client,auth_headers,fake_llm):
    r=await client.post(
        "/api/v1/chat",
        json={"question":"看看响应头","temperature":0.3},
        headers=auth_headers,
        )

    assert "X-RateLimit-Limit" in r.headers
    assert "X-RateLimit-Remaining" in r.headers

