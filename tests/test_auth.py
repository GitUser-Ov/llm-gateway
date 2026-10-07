import pytest

async def test_missing_api_key_returns_401(client):
    resp=await client.get("/api/v1/conversations")
    assert resp.status_code==401
    assert resp.json()["error"]["code"]=="unauthorized"

async def test_wrong_api_key_returns_401(client):
    resp=await client.get(
        "/api/v1/conversations",headers={"X-API-Key":"sk-llm-totally-wrong"}
    )

    assert resp.status_code==401

async def test_inactive_user_returns_401(client,user_and_key,test_maker):
    from sqlalchemy import select
    from app.db.models import User
    from app.core.security import hash_api_key

    raw_key="sk-llm-inactive-test-key"
    async with test_maker() as session:
        session.add(User(username="封禁用户",api_key_hash=hash_api_key(raw_key),is_active=False))
        await session.commit()

    resp=await client.get("/api/v1/conversations",headers={"X-API-Key":raw_key})
    assert resp.status_code==401
    assert resp.json()["error"]["message"]=="该账号已被禁用"

async def test_error_message_does_not_leak_details(client):
    resp=await client.get("/api/v1/conversations",headers={"X-API-Key":"bad"})
    msg=resp.json()["error"]["message"]
    assert msg=="API Key 无效"

