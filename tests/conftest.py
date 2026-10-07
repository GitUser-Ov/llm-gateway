import os
import asyncio
import sys

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from httpx import ASGITransport,AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine

os.environ.setdefault("ENV","test")
os.environ.setdefault("LOG_LEVEL","WARNING")

from app.api.deps import get_db,get_redis
from app.core.config import get_settings
from app.core.security import generate_api_key,hash_api_key
from app.db.base import Base
import app.db.models
from app.db.models import User
from app.main import app

settings=get_settings()

def pytest_asyncio_loop_factories(config,item):
    if sys.platform=="win32":
        return {"selector":asyncio.SelectorEventLoop}
    return {"stdlib":asyncio.new_event_loop}

TEST_DB_URL=os.getenv("TEST_DATABASE_URL")or(settings.database_url.rsplit("/",1)[0]+"/campus_test")

TEST_REDIS_URL=settings.redis_url.rsplit("/",1)[0]+"/15"

@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine=create_async_engine(TEST_DB_URL,echo=False,pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest_asyncio.fixture
async def test_maker(test_engine):
    return async_sessionmaker(test_engine,expire_on_commit=False)

@pytest_asyncio.fixture
async def test_redis():
    client=aioredis.from_url(TEST_REDIS_URL,decode_responses=True,single_connection_client=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()

@pytest_asyncio.fixture
async def client(test_maker,test_redis,monkeypatch):
    async def _get_db():
        async with test_maker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
    app.dependency_overrides[get_db]=_get_db

    app.dependency_overrides[get_redis]=lambda:test_redis
    monkeypatch.setattr("app.api.deps.get_redis",lambda:test_redis)

    monkeypatch.setattr("app.services.chat_service.SessionLocal",test_maker)

    transport=ASGITransport(app=app)
    async with AsyncClient(transport=transport,base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()

@pytest_asyncio.fixture
async def user_and_key(test_maker):
    raw_key=generate_api_key()
    async with test_maker() as session:
        user=User(username="测试用户",api_key_hash=hash_api_key(raw_key))
        session.add(user)
        await session.commit()
        await session.refresh(user)

    return user,raw_key

@pytest.fixture
def auth_headers(user_and_key):
    _,raw_key=user_and_key
    return {"X-API-Key":raw_key}


class FakeLLM:
    def __init__(self,answer:str="这是一个测试回答。",fail_with:Exception|None=None):
        self.answer=answer
        self.fail_with=fail_with
        self.calls:list[list[dict]]=[]

    async def chat(self,messages:list[dict],temperature:float=0.7,usage=None)->str:
        self.calls.append(messages)
        if self.fail_with is not None:
            raise self.fail_with
        if usage is not None:
            usage.prompt_tokens=11
            usage.completion_tokens=22
            usage.model="fake-model"
            usage.upstream="fake"
        return self.answer

    async def stream_chat(self,messages:list[dict],temperature:float=0.7,usage=None):
        self.calls.append(messages)
        if self.fail_with is not None:
            raise self.fail_with
        if usage is not None:
            usage.prompt_tokens=33
            usage.completion_tokens=44
            usage.model="fake-model"
            usage.upstream="fake"
        for ch in self.answer:
            yield ch

@pytest.fixture
def fake_llm(monkeypatch):
    fake=FakeLLM()
    monkeypatch.setattr("app.llm.client.chat",fake.chat)
    monkeypatch.setattr("app.llm.client.stream_chat",fake.stream_chat)
    return fake

