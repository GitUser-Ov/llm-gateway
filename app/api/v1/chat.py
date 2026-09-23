"""聊天接口。"""
from fastapi import APIRouter
from app.api.deps import CurrentUserDep, DbDep, SettingsDep
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import answer_in_conversation
import json
import logging
from collections.abc import Iterator
from fastapi.responses import StreamingResponse
from app.core.errors import AppError
from app.llm import client as llm
from app.services import chat_service as svc
logger=logging.getLogger(__name__)

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

def sse(playload:dict)->str:
    return f"data:{json.dumps(playload,ensure_ascii=False)}\n\n"

def stream_generator(
    conv_id:int,
    messages:list[dict],
    temperature:float,
)->Iterator[str]:
    yield sse({"type":"meta","conversation_id":conv_id})
    parts:list[str]=[]
    try:
        for delta in llm.stream_chat(messages,temperature=temperature):
            parts.append(delta)
            yield sse({"type":"delta","content":delta})
    except Exception:
        logger.exception("流式调用失败 conversation_id=%s",conv_id)
        yield sse({
            "type":"error",
            "code":"upstream_error",
            "message":"模型服务暂时不可用，请稍后重试",
        })
        return

    answer=" ".join(parts)
    if not answer.strip():
        yield sse({"type":"error","code":"empty_answer","message":"模型没有返回内容"})
        return

    try:
        message_id=svc.persist_assistant_message(conv_id,answer)
    except Exception:
        logger.exception("保存回答失败 conversation_id=%s",conv_id)
        yield sse({"type":"error","code":"persist_failed","message":"回答保存失败"})
        return

    yield sse({"type":"done","conversation_id":conv_id,"message_id":message_id})

@router.post("/stream",summary="流式问答（SSE）")
def chat_stream(req:ChatRequest,db:DbDep,user:CurrentUserDep)->StreamingResponse:
    conv_id,messages=svc.prepare_turn(
        db,user,question=req.question,conversation_id=req.conversation_id
    )

    return StreamingResponse(
        stream_generator(conv_id,messages,req.temperature),
        media_type="text/event-stream",
        headers={
            "Cache-Control":"no-cache",
            "Connection":"keep-alive",
            "X-Accel-Buffering":"no",
        },
    )