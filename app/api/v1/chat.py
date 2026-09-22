"""聊天接口。"""
from fastapi import APIRouter
from app.api.deps import CurrentUserDep, DbDep, SettingsDep
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import answer_in_conversation

router = APIRouter(prefix="/chat", tags=["聊天"])

@router.post("", response_model=ChatResponse, summary="单轮问答（非流式）")
def chat(
    req: ChatRequest,
    db: DbDep,
    user: CurrentUserDep,
    settings: SettingsDep,
) -> ChatResponse:
    conv, assistant_msg = answer_in_conversation(
        db,
        user,
        question=req.question,
        conversation_id=req.conversation_id,
        temperature=req.temperature,
    )
    return ChatResponse(
        conversation_id=conv.id,
        message_id=assistant_msg.id,
        answer=assistant_msg.content,
        model=settings.llm_model,
    )