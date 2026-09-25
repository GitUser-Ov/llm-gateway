import pytest

from app.core.security import generate_api_key,hash_api_key
from app.db.models import User

async def _make_user(test_maker,name:str)->str:
    raw=generate_api_key()
    async with test_maker() as session:
        session.add(User(username=name,api_key_hash=hash_api_key(raw)))
        await session.commit()
    return raw

async def test_create_and_list_conversations(client,auth_headers):
    r=await client.post("/api/v1/conversations",json={"title":"我的第一个会话"},headers=auth_headers)
    assert r.status_code==200
    cid=r.json()["id"]

    lst=await client.get("/api/v1/conversations",headers=auth_headers)
    assert lst.status_code==200
    body=lst.json()
    assert body["total"]==1
    assert body["page"]==1
    assert body["size"]==20
    assert body["items"][0]["id"]==cid
    assert body["items"][0]["title"]=="我的第一个会话"

async def test_cross_user_access_returns_404(client,auth_headers,test_maker,fake_llm):
    r=await client.post(
        "/api/v1/chat",
        json={"question":"张三的秘密问题","temperature":0.3},
        headers=auth_headers,
    )

    cid=r.json()["conversation_id"]

    other_key=await _make_user(test_maker,"李四")
    r2=await client.get(
        f"/api/v1/conversations/{cid}",headers={"X-API-Key":other_key}
    )
    assert r2.status_code==404
    assert r2.json()["error"]["code"]=="not_found"
    assert "不存在" in r2.json()["error"]["message"]

    r3=await client.get(
        f"/api/v1/conversations/{cid}/messages",headers={"X-API-Key":other_key}
    )
    assert r3.status_code==404

async def test_delete_conversation_cascades(client,auth_headers,test_maker,fake_llm):
    r=await client.post(
        "/api/v1/chat",
        json={"question":"要被删掉的会话","temperature":0.3},
        headers=auth_headers,
    )

    cid=r.json()["conversation_id"]

    d=await client.delete(f"/api/v1/conversations/{cid}",headers=auth_headers)
    assert d.status_code==204

    g=await client.get(f"/api/v1/conversations/{cid}",headers=auth_headers)
    assert g.status_code==404

    from sqlalchemy import func,select
    from app.db.models import Message

    async with test_maker() as session:
        count=await session.scalar(
            select(func.count()).select_from(Message).where(Message.conversation_id==cid)

        )
    assert count==0

async def test_pagination(client,auth_headers):
    for i in range(5):
        await client.post("/api/v1/conversations",json={"title":f"会话{i}"},headers=auth_headers)

    p1=await client.get("/api/v1/conversations?page=1&size=2",headers=auth_headers)
    assert len(p1.json()["items"])==2
    assert p1.json()["total"]==5

    p3=await client.get("/api/v1/conversations?page=3&size=2",headers=auth_headers)
    assert len(p3.json()["items"])==1

async def test_delete_others_conversation_returns_404(client,auth_headers,test_maker,fake_llm):
    r=await client.post(
        "/api/v1/chat",
        json={"question":"张三要删除的会话","temperature":0.3},
        headers=auth_headers,
    )

    cid=r.json()["conversation_id"]

    other_key=await _make_user(test_maker,"李四")
    d=await client.delete(
        f"/api/v1/conversations/{cid}",headers={"X-API-Key":other_key}
    )
    assert d.status_code==404
    assert d.json()["error"]["code"]=="not_found"


