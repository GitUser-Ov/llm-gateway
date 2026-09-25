import pytest
from app.core.errors import UpstreamError
from tests.conftest import FakeLLM

async def test_chat_creates_conversation_and_persists(client,auth_headers,fake_llm):
    resp=await client.post(
        "/api/v1/chat",
        json={"question":"缓考怎么申请?","temperature":0.3},
        headers=auth_headers,
    )
    assert resp.status_code==200
    data=resp.json()
    assert data["conversation_id"]>0
    assert data["message_id"]>0
    assert data["answer"]==fake_llm.answer
    assert data["from_cache"] is False
    assert len(fake_llm.calls)==1

    conv=await client.get(
        f"/api/v1/conversations/{data['conversation_id']}",headers=auth_headers
    )

    assert conv.json()["title"].startswith("缓考怎么申请")

    assert len(conv.json()["messages"])==2

async def test_cache_hit_avoids_model_call(client,auth_headers,fake_llm):
    payload={"question":"缓考怎么申请？","temperature":0.3}

    r1=await client.post("/api/v1/chat",json=payload,headers=auth_headers)
    assert r1.status_code==200
    assert r1.json()["from_cache"] is False
    assert len(fake_llm.calls)==1

    r2=await client.post("/api/v1/chat",json=payload,headers=auth_headers)
    assert r2.status_code==200
    assert r2.json()["from_cache"] is True
    assert r2.json()["answer"]==r1.json()["answer"]

    assert len(fake_llm.calls)==1

async def test_same_conversation_does_not_hit_cache(client,auth_headers,fake_llm):
    payload={"question":"缓考怎么申请？","temperature":0.3}
    r1=await client.post("/api/v1/chat",json=payload,headers=auth_headers)
    cid=r1.json()["conversation_id"]

    r2=await client.post(
        "/api/v1/chat",json={**payload,"conversation_id":cid},headers=auth_headers
        )
    assert r2.json()["from_cache"] is False
    assert len(fake_llm.calls)==2

async def test_model_failure_degrades_gracefully(client,auth_headers,monkeypatch):
    fake=FakeLLM(fail_with=UpstreamError("模型炸了"))
    monkeypatch.setattr("app.llm.client.chat",fake.chat)

    resp=await client.post(
        "/api/v1/chat",
        json={"question":"随便问点什么","temperature":0.3},
        headers=auth_headers,
        )
    assert resp.status_code==200
    assert "暂时不可用" in resp.json()["answer"]
    assert len(fake.calls)==1

async def test_degraded_answer_not_cached(client,auth_headers,monkeypatch):
    class FlakyLLM:
        def __init__(self):
            self.calls=0

        async def chat(self,messages,temperature=0.7):
            self.calls+=1
            if self.calls==1:
                raise UpstreamError("第一次失败")
            return "恢复正常了"

    flaky=FlakyLLM()
    monkeypatch.setattr("app.llm.client.chat",flaky.chat)

    payload={"question":"缓存污染测试","temperature":0.3}
    r1=await client.post("/api/v1/chat",json=payload,headers=auth_headers)
    assert "暂时不可用" in r1.json()["answer"]

    r2=await client.post("/api/v1/chat",json=payload,headers=auth_headers)
    assert r2.json()["answer"]=="恢复正常了"
    assert flaky.calls==2

async def test_chat_stream_sends_sse_events(client,auth_headers,fake_llm):
    fake_llm.answer="流式回答"
    resp=await client.post(
        "/api/v1/chat/stream",
        json={"question":"讲讲 RAG","temperature":0.3},
        headers=auth_headers,
    )

    assert resp.status_code==200
    assert resp.headers["content-type"].startswith("text/event-stream")

    text=resp.text
    assert'"type": "meta"'in text or '"type":"meta"' in text
    assert'"type": "delta"'in text or '"type":"delta"' in text
    assert'"type": "done"'in text or '"type":"done"' in text

    for ch in "流式回答":
        assert f'"content": "{ch}"' in text


