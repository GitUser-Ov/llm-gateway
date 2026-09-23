"""会话接口（异步版）。"""
from fastapi import APIRouter, Query
from app.api.deps import CurrentUserDep, DbDep
from app.schemas.conversation import (
    ConversationCreate, ConversationDetail, ConversationOut, MessageOut, Page,
)
from app.services import conversation_service as svc

router = APIRouter(prefix="/conversations", tags=["会话"])

@router.post("", response_model=ConversationOut, summary="新建会话")
async def create_conversation(
    payload: ConversationCreate, db: DbDep, user: CurrentUserDep
) -> ConversationOut:
    conv = await svc.create_conversation(db, user, payload)
    return ConversationOut.model_validate(conv)

@router.get("", response_model=Page[ConversationOut], summary="会话列表（分页）")
async def list_conversations(
    db: DbDep,
    user: CurrentUserDep,
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    size: int = Query(20, ge=1, le=100, description="每页条数，最多 100"),
) -> Page[ConversationOut]:
    return await svc.list_conversations(db, user, page=page, size=size)

@router.get("/{conversation_id}", response_model=ConversationDetail, summary="会话详情")
async def get_conversation(
    conversation_id: int, db: DbDep, user: CurrentUserDep
) -> ConversationDetail:
    conv = await svc.get_conversation(db, user, conversation_id)
    messages = [MessageOut.model_validate(m) for m in conv.messages]
    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        message_count=len(messages),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages,
    )

@router.delete("/{conversation_id}", status_code=204, summary="删除会话")
async def delete_conversation(
    conversation_id: int, db: DbDep, user: CurrentUserDep
) -> None:
    await svc.delete_conversation(db, user, conversation_id)
    return None

@router.get(
    "/{conversation_id}/messages",
    response_model=Page[MessageOut],
    summary="会话消息列表（分页）",
)
async def list_messages(
    conversation_id: int,
    db: DbDep,
    user: CurrentUserDep,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
) -> Page[MessageOut]:
    return await svc.list_messages(db, user, conversation_id, page=page, size=size)