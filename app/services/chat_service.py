"""业务层：把"回答一个问题"这件事完整做一遍（异步版）。"""
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.errors import BadRequestError, NotFoundError, UpstreamError
from app.db.base import SessionLocal
from app.db.models import Conversation, Message, User
from app.llm import client as llm
from app.llm.prompts import (
    NO_CONTEXT_ANSWER,
    RAG_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_rag_user_message,
)
from app.rag import service as rag
from app.services.conversation_service import make_title
from app.cache import answer_cache
from app.cache.redis_client import get_redis
from dataclasses import dataclass, field
import redis.asyncio as aioredis

@dataclass
class AnswerResult:
    conversation:Conversation
    message:Message
    from_cache:bool=False
    degraded:bool=False
    usage:llm.Usage|None=None
    citations:list[str]=field(default_factory=list)

logger = logging.getLogger(__name__)
settings = get_settings()
MAX_CONTEXT_MESSAGES = 20

async def retrieve_context(
    db: AsyncSession, question: str
) -> list[rag.RetrievedChunk]:
    """检索知识库；未启用或检索失败时返回空列表，不影响主流程。"""
    if not settings.rag_enabled:
        return []
    try:
        return await rag.search(db, question)
    except Exception:
        # 检索是增强能力，不该让整个问答挂掉
        logger.exception("知识库检索失败，按无资料处理")
        return []

async def build_context(
    db: AsyncSession, conversation_id: int, limit: int = MAX_CONTEXT_MESSAGES
) -> list[dict]:
    """取出最近 limit 条消息，转成模型要的格式。"""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    rows = list(reversed((await db.scalars(stmt)).all()))
    return [{"role": m.role, "content": m.content} for m in rows]

async def _resolve_conversation(
    db: AsyncSession, user: User, question: str, conversation_id: int | None
) -> Conversation:
    """找到或新建会话。"""
    if conversation_id is None:
        conv = Conversation(user_id=user.id, title=make_title(question))
        db.add(conv)
        await db.flush()             # 拿到 id，但还没提交
        return conv
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFoundError("会话不存在")
    return conv

async def answer_in_conversation(
    db: AsyncSession,
    user: User,
    question: str,
    conversation_id: int | None = None,
    temperature: float = 0.7,
    redis:aioredis.Redis|None=None,
    use_cache:bool=True,
) -> AnswerResult:
    """非流式问答：一个事务里做完所有数据库操作。"""
    redis=redis or get_redis()  
        
    question = (question or "").strip()
    if not question:
        raise BadRequestError("问题内容不能为空")
    conv = await _resolve_conversation(db, user, question, conversation_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=question)
    db.add(user_msg)
    await db.flush()

    # ★ RAG：先检索，再决定要不要调模型
    chunks = await retrieve_context(db, question)
    citations = [c.citation for c in chunks]

    if not chunks and settings.rag_enabled and settings.rag_refuse_when_empty:
        # 没有依据就直接拒答：既省 token，也避免模型自由发挥产生幻觉
        answer = NO_CONTEXT_ANSWER
        usage = llm.Usage()
        assistant_msg = Message(
            conversation_id=conv.id, role="assistant", content=answer,
            prompt_tokens=0, completion_tokens=0,
        )
        db.add(assistant_msg)
        conv.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(assistant_msg)
        await db.refresh(conv)
        logger.info(
            "无检索资料，直接拒答",
            extra={"extra_fields": {"conversation_id": conv.id, "rag": "empty"}},
        )
        return AnswerResult(
            conversation=conv, message=assistant_msg,
            from_cache=False, degraded=False, usage=usage, citations=[],
        )

    if chunks:
        history = await build_context(db, conv.id)
        messages = [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
            # 只保留最近若干轮，检索资料放在最后一条里
            *history[:-1],
            {"role": "user", "content": build_rag_user_message(
                question, [c.content for c in chunks]
            )},
        ]
    else:
        history = await build_context(db, conv.id)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    # ★ 缓存 key 必须带上检索到的资料：同一句话在资料变化后答案应该变化，
    #   只按 messages 缓存会在知识库更新后返回过期答案。
    cached:str|None=None
    if use_cache:
        cached=await answer_cache.get_answer(
            redis,messages,settings.primary_model,temperature
        )

    usage=llm.Usage()
    if cached is not None:
        answer=cached
        degraded=False
        from_cache=True
    else:
        from_cache=False
        
    # ★ 模型调用失败时，用降级文案顶上，而不是把 502 甩给用户
        try:
            answer = await llm.chat(messages, temperature=temperature, usage=usage)
            degraded = False
        except UpstreamError:
            logger.exception("模型调用失败，启用降级文案 conversation_id=%s", conv.id)
            answer = settings.llm_fallback_message
            degraded = True
        if use_cache and not degraded:
            await answer_cache.set_answer(
                redis,messages,settings.primary_model,temperature,answer
            )
    assistant_msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content=answer,
        # ★ 用量计量：只有真正调用过模型才有值，缓存命中和降级都是 0
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
    )
    db.add(assistant_msg)
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(assistant_msg)
    await db.refresh(conv)
    logger.info(
        "问答完成",
        extra={"extra_fields":{
            "conversation_id":conv.id,
            "from_cache":from_cache,
            "degraded":degraded,
            "upstream":usage.upstream,
            "model":usage.model,
            "prompt_tokens":usage.prompt_tokens,
            "completion_tokens":usage.completion_tokens,
            "fallbacks":usage.fallbacks,
            "rag_chunks":len(chunks),
            "rag_top_score":chunks[0].score if chunks else None,
        }},
    )
    if degraded:
        logger.warning("本次回答为降级结果 conversation_id=%s", conv.id)
    return AnswerResult(
        conversation=conv,
        message=assistant_msg,
        from_cache=from_cache,
        degraded=degraded,
        usage=usage,
        citations=citations,
    )

# ---------------------------------------------------------------
# 流式问答：把"准备"和"输出"切开
# ---------------------------------------------------------------
async def prepare_turn(
    db: AsyncSession, user: User, question: str, conversation_id: int | None = None
) -> tuple[int, list[dict], list[str]]:
    """流式问答的第一步：同步完成所有数据库操作 + 检索，返回 (会话 id, messages, 引用)。

    ★ 检索放在这里（而不是生成器里）是刻意的：检索需要数据库会话，
    而流开始后请求级会话已经释放。
    """
    question = (question or "").strip()
    if not question:
        raise BadRequestError("问题内容不能为空")
    conv = await _resolve_conversation(db, user, question, conversation_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=question)
    db.add(user_msg)
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()

    chunks = await retrieve_context(db, question)
    citations = [c.citation for c in chunks]
    history = await build_context(db, conv.id)

    if chunks:
        messages = [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
            *history[:-1],
            {"role": "user", "content": build_rag_user_message(
                question, [c.content for c in chunks]
            )},
        ]
    elif settings.rag_enabled and settings.rag_refuse_when_empty:
        # 无资料：返回空 messages，表示"不要调模型，直接给固定拒答文案"
        messages = []
    else:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    return conv.id, messages, citations

async def persist_assistant_message(
    conversation_id: int, content: str, usage: llm.Usage | None = None
) -> int:
    """流结束后，用一个独立的短会话把回答写进数据库。返回消息 id。"""
    async with SessionLocal() as db:
        msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )
        db.add(msg)
        conv = await db.get(Conversation, conversation_id)
        if conv is not None:
            conv.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(msg)
        return msg.id

async def stream_answer(
    conv_id: int, messages: list[dict], temperature: float,
    usage: llm.Usage | None = None,
):
    """流式生成器：逐段产出 SSE 需要的文本增量。
    这个生成器**只负责模型调用和降级**，SSE 的包装在 api 层做。
    用量就地累加到传入的 usage 对象上（同一个请求内是同一个对象）。

    messages 为空表示"没有检索到资料、且配置为拒答"——此时不调模型，
    直接把固定文案逐字吐出，保持前端流式体验一致。
    """
    if not messages:
        for ch in NO_CONTEXT_ANSWER:
            yield ch
        return
    try:
        async for delta in llm.stream_chat(messages, temperature=temperature, usage=usage):
            yield delta
    except UpstreamError:
        logger.exception("流式调用失败，启用降级文案 conversation_id=%s", conv_id)
        # 把降级文案也"流"出去，前端体验一致
        for ch in settings.llm_fallback_message:
            yield ch
        raise          # 让上层知道发生过失败（用于记录/标记）

