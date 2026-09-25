"""聊天接口（异步版）。"""
import json
import logging
from collections.abc import AsyncIterator
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.api.deps import CurrentUserDep, DbDep, SettingsDep
from app.core.errors import UpstreamError
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import chat_service as svc
from app.api.deps import CurrentUserDep,DbDep,RateLimitDep,QuotaDep,SettingsDep,RedisDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["聊天"])

@router.post("", response_model=ChatResponse, summary="单轮问答（非流式）")
async def chat(
    req: ChatRequest, db: DbDep, user: CurrentUserDep, settings: SettingsDep,_rl:RateLimitDep,_quota:QuotaDep,redis:RedisDep,
) -> ChatResponse:
    result = await svc.answer_in_conversation(
        db, user,
        question=req.question,
        conversation_id=req.conversation_id,
        temperature=req.temperature,
        redis=redis,
    )
    return ChatResponse(
        conversation_id=result.conversation.id,
        message_id=result.message.id,
        answer=result.message.content,
        model=settings.llm_model,
        from_cache=result.from_cache
    )

def sse(payload: dict) -> str:
    """把一个 dict 包成一条 SSE 消息（结尾必须两个换行）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

async def stream_generator(
    conv_id: int, messages: list[dict], temperature: float
) -> AsyncIterator[str]:
    """把模型输出逐段转成 SSE 文本。
    注意：这里已经不在请求级的数据库会话里了（第 9 章讲过为什么）。
    落库用 svc.persist_assistant_message（它自己开短会话）。
    """
    yield sse({"type": "meta", "conversation_id": conv_id})
    parts: list[str] = []
    failed = False
    try:
        async for delta in svc.stream_answer(conv_id, messages, temperature):
            parts.append(delta)
            yield sse({"type": "delta", "content": delta})
    except UpstreamError as exc:
        failed = True
        yield sse({
            "type": "error",
            "code": exc.code,
            "message": exc.message,
            # ★ 降级文案已经作为 delta 发出去了，这里只是告知前端"这次是降级结果"
            "degraded": True,
        })
    answer = "".join(parts)
    if not answer.strip():
        yield sse({"type": "error", "code": "empty_answer", "message": "模型没有返回内容"})
        return
    try:
        message_id = await svc.persist_assistant_message(conv_id, answer)
    except Exception:
        logger.exception("保存回答失败 conversation_id=%s", conv_id)
        yield sse({"type": "error", "code": "persist_failed", "message": "回答保存失败"})
        return
    yield sse({
        "type": "done",
        "conversation_id": conv_id,
        "message_id": message_id,
        "degraded": failed,
    })

@router.post("/stream", summary="流式问答（SSE）")
async def chat_stream(req: ChatRequest, db: DbDep, user: CurrentUserDep,_rl:RateLimitDep,_quota:QuotaDep) -> StreamingResponse:
    # ★ 所有可能失败的检查都在流开始前做完（这时还能返回正常的 4xx）
    conv_id, messages = await svc.prepare_turn(
        db, user, question=req.question, conversation_id=req.conversation_id
    )
    return StreamingResponse(
        stream_generator(conv_id, messages, req.temperature),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )