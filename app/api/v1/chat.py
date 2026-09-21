from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.chat import ChatRequest,ChatResponse
from app.services.chat_service import answer_once

router=APIRouter(prefix="/chat",tags=["聊天"])

@router.post("",response_model=ChatResponse,summary="单轮问答（非流式）")

def chat(req:ChatRequest)->ChatResponse:
    answer=answer_once(req.question,temperature=req.temperature)
    return ChatResponse(answer=answer,model=get_settings().llm_model)

    