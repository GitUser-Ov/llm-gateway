"""会话相关的业务逻辑（异步版）。"""
import logging
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.core.errors import NotFoundError
from app.db.models import Conversation, Message, User
from app.schemas.conversation import ConversationCreate, ConversationOut, Page

logger = logging.getLogger(__name__)
MAX_TITLE_LEN = 20

async def _owned_conversation(db: AsyncSession, user: User, conversation_id: int) -> Conversation:
    """取出属于该用户的会话；不存在或不属于他，一律 404。"""
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFoundError("会话不存在")
    return conv

def make_title(question: str) -> str:
    text = " ".join((question or "").split())
    if not text:
        return "新对话"
    return text[:MAX_TITLE_LEN] + ("…" if len(text) > MAX_TITLE_LEN else "")

async def create_conversation(
    db: AsyncSession, user: User, payload: ConversationCreate
) -> Conversation:
    conv = Conversation(user_id=user.id, title=payload.title or "新对话")
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    logger.info("创建会话 id=%s user_id=%s", conv.id, user.id)
    return conv

async def list_conversations(
    db: AsyncSession, user: User, page: int = 1, size: int = 20
) -> Page[ConversationOut]:
    total = await db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user.id)
    ) or 0
    stmt = (
        select(Conversation, func.count(Message.id))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user.id)
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(stmt)).all()
    items = [
        ConversationOut(
            id=conv.id, title=conv.title, message_count=count,
            created_at=conv.created_at, updated_at=conv.updated_at,
        )
        for conv, count in rows
    ]
    return Page[ConversationOut](items=items, total=total, page=page, size=size)

async def get_conversation(db: AsyncSession, user: User, conversation_id: int) -> Conversation:
    """取会话详情。★ 异步下必须显式预加载 messages，否则会 MissingGreenlet。"""
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
    )
    conv = await db.scalar(stmt)
    if conv is None or conv.user_id != user.id:
        raise NotFoundError("会话不存在")
    return conv

async def delete_conversation(db: AsyncSession, user: User, conversation_id: int) -> None:
    conv = await _owned_conversation(db, user, conversation_id)
    await db.delete(conv)
    await db.commit()
    logger.info("删除会话 id=%s user_id=%s", conversation_id, user.id)

async def list_messages(
    db: AsyncSession, user: User, conversation_id: int, page: int = 1, size: int = 50
) -> Page:
    await _owned_conversation(db, user, conversation_id)     # ★ 越权检查，不能省
    total = await db.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    ) or 0
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = list((await db.scalars(stmt)).all())
    return Page(items=rows, total=total, page=page, size=size)