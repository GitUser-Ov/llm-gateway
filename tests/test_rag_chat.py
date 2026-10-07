"""RAG 接入问答主流程的行为测试。

覆盖三件容易被忽略的事：
  1. 检索到资料时，发给模型的 prompt 里必须真的带上资料和引用要求；
  2. 检索不到资料时，应当直接拒答而不是让模型自由发挥；
  3. 引用来源要透出到接口响应 / SSE 事件里。
"""
import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.rag_models import Chunk, Document
from app.rag.embedder import FakeEmbedder
from app.rag.service import ingest_document

settings = get_settings()


def sse_deltas(text: str) -> str:
    """从 SSE 响应里把 delta 内容拼成完整回答。

    注意不能用 `"某段文字" in resp.text` 判断：流式是按字符或小段
    逐个事件推送的，字符串会被事件边界切开。
    """
    parts = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[len("data: "):])
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "delta":
            parts.append(payload.get("content", ""))
    return "".join(parts)


def sse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            events.append(json.loads(line[len("data: "):]))
        except json.JSONDecodeError:
            continue
    return events


@pytest.fixture(autouse=True)
async def enable_rag(monkeypatch, test_maker):
    """本文件专门测 RAG：打开开关、放宽阈值，并清空知识库。"""
    monkeypatch.setattr(settings, "rag_enabled", True, raising=False)
    monkeypatch.setattr(settings, "rag_min_score", -1.0, raising=False)
    monkeypatch.setattr(settings, "rag_refuse_when_empty", True, raising=False)
    async with test_maker() as db:
        await db.execute(Chunk.__table__.delete())
        await db.execute(Document.__table__.delete())
        await db.commit()
    yield


async def seed(test_maker, text: str, title: str = "测试文档") -> None:
    """把一段文本灌进（测试用的）知识库。"""
    e = FakeEmbedder(settings.embed_dim)
    async with test_maker() as db:
        await ingest_document(db, f"test://{title}", text, title=title, embedder=e)


async def test_context_is_injected_into_prompt(client, auth_headers, fake_llm, test_maker):
    """检索到资料时，prompt 必须包含资料正文与引用要求。"""
    await seed(test_maker, "# 部署说明\n服务通过 docker compose 启动，自动执行数据库迁移。", "部署说明")

    resp = await client.post(
        "/api/v1/chat",
        json={"question": "部署 说明 启动", "temperature": 0.3},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(fake_llm.calls) == 1

    sent = fake_llm.calls[0]                       # 发给模型的完整 messages
    joined = " ".join(m["content"] for m in sent)
    assert "部署说明" in joined                    # 资料正文进去了
    assert "[1]" in joined                         # 带了编号，便于引用
    assert any("参考资料" in m["content"] for m in sent)
    assert sent[0]["role"] == "system"
    assert "只能依据" in sent[0]["content"]        # RAG 的硬约束生效


async def test_citations_are_returned(client, auth_headers, fake_llm, test_maker):
    """引用来源要透出到响应里。"""
    await seed(test_maker, "# 部署说明\n服务通过 docker compose 启动，自动执行数据库迁移。", "部署说明")

    resp = await client.post(
        "/api/v1/chat",
        json={"question": "部署 说明 启动", "temperature": 0.3},
        headers=auth_headers,
    )
    data = resp.json()
    assert data["citations"], "应当返回至少一条引用"
    assert any("部署说明" in c for c in data["citations"])


async def test_refuses_without_context(client, auth_headers, fake_llm):
    """知识库为空时直接拒答，且不调用模型——省 token 也避免幻觉。"""
    resp = await client.post(
        "/api/v1/chat",
        json={"question": "知识库里完全没有的内容", "temperature": 0.3},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "无法回答" in data["answer"]
    assert data["citations"] == []
    assert fake_llm.calls == []          # ★ 没有调用模型
    assert data["prompt_tokens"] == 0
    assert data["completion_tokens"] == 0


async def test_refuse_can_be_disabled(client, auth_headers, fake_llm, monkeypatch):
    """关掉拒答开关后，无资料时应回退到普通问答（仍会调用模型）。"""
    monkeypatch.setattr(settings, "rag_refuse_when_empty", False, raising=False)

    resp = await client.post(
        "/api/v1/chat",
        json={"question": "知识库里没有的问题", "temperature": 0.3},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert fake_llm.calls, "关掉拒答后应当照常调用模型"
    assert "无法回答" not in resp.json()["answer"]


async def test_stream_meta_event_carries_citations(client, auth_headers, fake_llm, test_maker):
    """流式场景下，引用要在首个 meta 事件就推给前端。"""
    await seed(test_maker, "# 部署说明\n服务通过 docker compose 启动，自动执行数据库迁移。", "部署说明")

    resp = await client.post(
        "/api/v1/chat/stream",
        json={"question": "部署 说明 启动", "temperature": 0.3},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    events = sse_events(resp.text)
    meta = next(e for e in events if e["type"] == "meta")
    assert meta["citations"]
    assert any("部署说明" in c for c in meta["citations"])


async def test_stream_refuses_without_context(client, auth_headers, fake_llm):
    """流式场景下无资料也要拒答，且不调模型。"""
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"question": "知识库里没有的内容", "temperature": 0.3},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "无法回答" in sse_deltas(resp.text)   # 拼接 delta 后再断言
    assert fake_llm.calls == []                  # 没调模型
