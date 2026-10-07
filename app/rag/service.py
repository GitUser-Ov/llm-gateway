"""知识库检索服务。

职责：
  - ingest：把文档切片、向量化、入库（按 source 幂等，重复灌入是更新）
  - search：把问题向量化，按余弦相似度取 top-k，返回带来源的片段

不负责：拼 prompt、调模型。那些属于 chat_service 的编排职责。
"""
import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.rag_models import Chunk, Document
from app.rag.chunker import chunk_text
from app.rag.embedder import Embedder, get_embedder

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class RetrievedChunk:
    """一条检索结果。source/title 用于在回答里标注引用来源。"""

    chunk_id: int
    document_id: int
    source: str
    title: str
    heading: str
    content: str
    score: float

    @property
    def citation(self) -> str:
        label = self.title or self.source
        if self.heading:
            return f"{label} · {self.heading}"
        return label


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def ingest_document(
    db: AsyncSession,
    source: str,
    text: str,
    title: str = "",
    embedder: Embedder | None = None,
) -> Document:
    """把一份文档灌入知识库（幂等：同一 source 重复灌入会替换旧切片）。"""
    embedder = embedder or get_embedder()
    text = text or ""
    if not text.strip():
        raise ValueError("文档内容为空")
    if len(text) < settings.chunk_size / 4:
        logger.warning("文档过短，可能切不出有价值的块 source=%s len=%s", source, len(text))

    pieces = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
    if not pieces:
        raise ValueError("切片结果为空")

    vectors = embedder.encode([p.content for p in pieces])

    # 幂等：已存在则清掉旧切片
    existing = await db.scalar(select(Document).where(Document.source == source))
    if existing is not None:
        await db.delete(existing)      # cascade 会一并删掉旧 chunks
        await db.flush()

    doc = Document(
        source=source,
        title=title or source,
        content_hash=_hash(text),
        chunk_count=len(pieces),
    )
    db.add(doc)
    await db.flush()

    for piece, vec in zip(pieces, vectors):
        db.add(Chunk(
            document_id=doc.id,
            idx=piece.index,
            heading=piece.heading,
            content=piece.content,
            embedding=np_to_list(vec),
        ))
    await db.commit()
    await db.refresh(doc)
    logger.info(
        "文档已灌入知识库",
        extra={"extra_fields": {
            "source": source, "chunks": len(pieces), "embedder": embedder.name,
        }},
    )
    return doc


def np_to_list(vec) -> list[float]:
    """把 numpy 向量转成 list[float]，交给驱动时避免隐式类型问题。"""
    try:
        return [float(x) for x in vec]
    except TypeError:
        return list(vec)


async def search(
    db: AsyncSession,
    query: str,
    top_k: int | None = None,
    embedder: Embedder | None = None,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    """按余弦相似度检索最相关的切片。

    pgvector 的 <=> 是余弦距离（越小越相似），
    相似度 = 1 - 距离，因此下面按 distance 升序排。
    """
    query = (query or "").strip()
    if not query:
        return []

    top_k = top_k or settings.rag_top_k
    min_score = settings.rag_min_score if min_score is None else min_score
    embedder = embedder or get_embedder()
    qvec = np_to_list(embedder.encode_one(query))

    distance = Chunk.embedding.cosine_distance(qvec).label("distance")
    stmt = (
        select(Chunk, Document, distance)
        .join(Document, Document.id == Chunk.document_id)
        .order_by(distance)
        .limit(top_k)
    )
    rows = (await db.execute(stmt)).all()

    results: list[RetrievedChunk] = []
    for chunk, doc, dist in rows:
        score = 1.0 - float(dist)
        if score < min_score:
            continue
        results.append(RetrievedChunk(
            chunk_id=chunk.id,
            document_id=doc.id,
            source=doc.source,
            title=doc.title,
            heading=chunk.heading,
            content=chunk.content,
            score=round(score, 4),
        ))
    logger.info(
        "知识库检索完成",
        extra={"extra_fields": {
            "query_len": len(query),
            "returned": len(results),
            "top_score": results[0].score if results else None,
        }},
    )
    return results
