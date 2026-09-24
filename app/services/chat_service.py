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
from app.llm.prompts import SYSTEM_PROMPT
from app.services.conversation_service import make_title
from app.cache import answer_cache
from app.cache.redis_client import get_redis
from dataclasses import dataclass


logger = logging.getLogger(__name__)
settings = get_settings()
MAX_CONTEXT_MESSAGES = 20

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
    use_cache:bool=True,
) -> tuple[Conversation, Message]:
    """非流式问答：一个事务里做完所有数据库操作。"""
    question = (question or "").strip()
    if not question:
        raise BadRequestError("问题内容不能为空")
    conv = await _resolve_conversation(db, user, question, conversation_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=question)
    db.add(user_msg)
    await db.flush()
    history = await build_context(db, conv.id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    redis=get_redis()
    cached:str|None=None
    if use_cache:
        cached=await answer_cache.get_answer(
            redis,messages,settings.llm_model,temperature
        )

    if cached is not None:
        answer=cached
        degraded=False
        from_cache=True
    else:
        from_cache=False
        
    # ★ 模型调用失败时，用降级文案顶上，而不是把 502 甩给用户
        try:
            answer = await llm.chat(messages, temperature=temperature)
            degraded = False
        except UpstreamError:
            logger.exception("模型调用失败，启用降级文案 conversation_id=%s", conv.id)
            answer = settings.llm_fallback_message
            degraded = True
        if use_cache and not degraded:
            await answer_cache.set_answer(
                redis,messages,settings.llm_model,temperature,answer
            )
    assistant_msg = Message(conversation_id=conv.id, role="assistant", content=answer)
    db.add(assistant_msg)
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(assistant_msg)
    await db.refresh(conv)
    logger.info(
        "问答完成",
        extra={"extra_fields":{"conversation_id":conv.id,"from_cache":from_cache,"degraded":degraded}},
    )
    if degraded:
        logger.warning("本次回答为降级结果 conversation_id=%s", conv.id)
    return AnswerResult(
        conversation=conv,
        message=assistant_msg,
        from_cache=from_cache,
        degraded=degraded
    )

# ---------------------------------------------------------------
# 流式问答：把"准备"和"输出"切开
# ---------------------------------------------------------------
async def prepare_turn(
    db: AsyncSession, user: User, question: str, conversation_id: int | None = None
) -> tuple[int, list[dict]]:
    """流式问答的第一步：同步完成所有数据库操作，返回 (会话 id, messages)。"""
    question = (question or "").strip()
    if not question:
        raise BadRequestError("问题内容不能为空")
    conv = await _resolve_conversation(db, user, question, conversation_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=question)
    db.add(user_msg)
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()
    history = await build_context(db, conv.id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    return conv.id, messages

async def persist_assistant_message(conversation_id: int, content: str) -> int:
    """流结束后，用一个独立的短会话把回答写进数据库。返回消息 id。"""
    async with SessionLocal() as db:
        msg = Message(conversation_id=conversation_id, role="assistant", content=content)
        db.add(msg)
        conv = await db.get(Conversation, conversation_id)
        if conv is not None:
            conv.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(msg)
        return msg.id

async def stream_answer(
    conv_id: int, messages: list[dict], temperature: float
):
    """流式生成器：逐段产出 SSE 需要的文本增量。
    这个生成器**只负责模型调用和降级**，SSE 的包装在 api 层做。
    """
    try:
        async for delta in llm.stream_chat(messages, temperature=temperature):
            yield delta
    except UpstreamError:
        logger.exception("流式调用失败，启用降级文案 conversation_id=%s", conv_id)
        # 把降级文案也"流"出去，前端体验一致
        for ch in settings.llm_fallback_message:
            yield ch
        raise          # 让上层知道发生过失败（用于记录/标记）

@dataclass
class AnswerResult:
    conversation:Conversation
    message:Message
    from_cache:bool=False
    degraded:bool=False