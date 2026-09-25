from types import SimpleNamespace
import httpx
import openai
import app.llm.client as llm_client

async def test_chat_retries_then_succeeds(monkeypatch):
    calls=0
    async def fake_create(**kwargs):
        nonlocal calls
        calls+=1
        if calls <=2:
            raise openai.APIConnectionError(
                request=httpx.Request("POST","http://test")
            )
        return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="重试成功"))]
        )

    fake_client=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )
    monkeypatch.setattr("app.llm.client._client_or_raise",lambda:fake_client)
    answer=await llm_client.chat(
        [{"role":"user","content":"你好"}],temperature=0.3
        )

    assert answer=="重试成功"
    assert calls==3
    