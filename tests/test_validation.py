import pytest
async def test_chat_empty_question_returns_422(client,auth_headers):
    resp=await client.post(
        "/api/v1/chat",
        json={"question":"","temperature":0.3},
        headers=auth_headers,
    )

    assert resp.status_code==422
    body=resp.json()
    assert body["error"]["code"]=="validation_error"
    fields=[d["field"] for d in body["error"]["details"]]
    assert "question" in fields

async def test_chat_too_long_question_returns_422(client,auth_headers):
    resp=await client.post(
        "/api/v1/chat",
        json={"question":"啊"*3000,"temperature":0.3},
        headers=auth_headers,
        )
    assert resp.status_code==422

async def test_chat_invalid_temperature_returns_422(client,auth_headers):
    resp=await client.post(
        "/api/v1/chat",
        json={"question":"你好","temperature":99},
        headers=auth_headers,
    )

    assert resp.status_code==422

async def test_conversation_list_size_limit(client,auth_headers):
    resp=await client.get("/api/v1/conversations?size=99999",headers=auth_headers)
    assert resp.status_code==422

async def test_question_length_boundary(client,auth_headers,fake_llm):
    ok=await client.post(
        "/api/v1/chat",
        json={"question":"啊"*2000,"temperature":0.3},
        headers=auth_headers,
        )

    assert ok.status_code==200

    bad=await client.post(
        "/api/v1/chat",
        json={"question":"啊"*2001,"temperature":0.3},
        headers=auth_headers,
    )

    assert bad.status_code==422


