"""RAG 全链路测试：切片 -> 向量化 -> 存储 -> 检索 -> 接入问答。

关键设计：用 FakeEmbedder 替换真实向量模型，因此这些用例
既不需要下载模型（92MB）、也不依赖 torch，跑得很快。

注意：FakeEmbedder 的维度必须与 settings.embed_dim 一致，
因为 chunks.embedding 列是按该维度定义的（vector(512)）。
"""
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.rag_models import Chunk, Document
from app.rag.chunker import chunk_text
from app.rag.embedder import FakeEmbedder
from app.rag.service import ingest_document, search

settings = get_settings()
DIM = settings.embed_dim


# ---------- 切片器 ----------

def test_chunker_respects_headings():
    text = "# 标题A\n内容A。" + "\n\n# 标题B\n内容B。"
    chunks = chunk_text(text, chunk_size=100, overlap=10)
    headings = [c.heading for c in chunks]
    assert "标题A" in headings
    assert "标题B" in headings


def test_chunker_merges_short_paragraphs():
    """短段落应被聚合，而不是一句一块。"""
    text = "\n\n".join(f"第{i}段内容。" for i in range(10))
    chunks = chunk_text(text, chunk_size=50, overlap=10)
    assert len(chunks) < 10


def test_chunker_splits_long_paragraph_with_overlap():
    text = "长句。" * 200          # 600 字，远超 chunk_size
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    assert all(len(c.content) <= 100 + 20 for c in chunks)


def test_chunker_rejects_overlap_ge_size():
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=10, overlap=10)


# ---------- 向量后端 ----------

def test_fake_embedder_is_deterministic_and_normalized():
    e = FakeEmbedder(dim=32)
    v1 = e.encode(["缓考怎么申请"])
    v2 = e.encode(["缓考怎么申请"])
    assert v1.shape == (1, 32)
    assert (v1 == v2).all()
    assert abs(float((v1 ** 2).sum()) - 1.0) < 1e-5      # 已归一化

    other = e.encode(["完全不同的内容"])
    assert not (v1 == other).all()


# ---------- 灌入与检索（走真实 pgvector）----------

@pytest.fixture
async def rag_db(test_maker):
    e = FakeEmbedder(dim=DIM)
    async with test_maker() as db:
        # 清空，避免用例间互相污染
        await db.execute(Chunk.__table__.delete())
        await db.execute(Document.__table__.delete())
        await db.commit()
        yield db, e


async def test_ingest_creates_document_and_chunks(rag_db):
    db, e = rag_db
    doc = await ingest_document(
        db, source="test://doc1", text="# 缓考\n学生需在考试前提交申请。",
        title="缓考规定", embedder=e,
    )
    assert doc.id > 0
    assert doc.chunk_count >= 1

    n_chunks = await db.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == doc.id)
    )
    assert n_chunks == doc.chunk_count


async def test_ingest_is_idempotent(rag_db):
    """同一个 source 重复灌入应替换旧内容，而不是累积。"""
    db, e = rag_db
    await ingest_document(db, "test://same", "# A\n第一次的内容。", embedder=e)
    first = await db.scalar(
        select(func.count()).select_from(Chunk).join(Document)
        .where(Document.source == "test://same")
    )
    await ingest_document(db, "test://same", "# A\n第二次的内容。", embedder=e)
    second = await db.scalar(
        select(func.count()).select_from(Chunk).join(Document)
        .where(Document.source == "test://same")
    )
    docs = await db.scalar(
        select(func.count()).select_from(Document).where(Document.source == "test://same")
    )

    assert docs == 1              # 只有一份文档
    assert first == second        # 切片数一致，没有翻倍


async def test_search_returns_citation_and_score(rag_db):
    db, e = rag_db
    await ingest_document(
        db, "test://campus", "# 缓考申请\n学生需在考试前向学院提交缓考申请。",
        title="缓考规定", embedder=e,
    )
    results = await search(db, "缓考申请流程", embedder=e, min_score=-1.0)
    assert results
    top = results[0]
    assert top.source == "test://campus"
    assert "缓考规定" in top.citation
    assert -1.0 <= top.score <= 1.0001


async def test_search_orders_by_similarity(rag_db):
    """更相似的文档应该排在前面。"""
    db, e = rag_db
    await ingest_document(db, "test://a", "缓考 申请 缓考 申请", title="A", embedder=e)
    await ingest_document(db, "test://b", "图书馆 开放 时间", title="B", embedder=e)

    results = await search(db, "图书馆 开放 时间", embedder=e, min_score=-1.0)
    assert results[0].title == "B"


async def test_search_empty_query_returns_nothing(rag_db):
    db, e = rag_db
    assert await search(db, "   ", embedder=e) == []


async def test_search_respects_min_score(rag_db):
    """min_score 设为很高的值时应当过滤掉所有结果。"""
    db, e = rag_db
    await ingest_document(db, "test://doc", "一些内容", embedder=e)
    results = await search(db, "完全无关的查询", embedder=e, min_score=0.999)
    assert results == []


async def test_ingest_rejects_empty_text(rag_db):
    db, e = rag_db
    with pytest.raises(ValueError):
        await ingest_document(db, "test://empty", "   ", embedder=e)
