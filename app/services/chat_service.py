from app.llm import client as llm
from app.llm.prompts import SYSTEM_PROMPT
from app.core.errors import BadRequestError
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import Conversation,Message,User
from app.services.conversation_service import make_title
from datetime import datetime, timezone
import logging
from app.db.base import SessionLocal
logger=logging.getLogger(__name__)

MAX_CONTEXT_MESSAGES=20

def build_context(db:Session,conversation_id:int,limit:int=MAX_CONTEXT_MESSAGES)->list[dict]:
    stmt=(
        select(Message)
        .where(Message.conversation_id==conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )

    rows=list(reversed(db.scalars(stmt).all()))
    return [{"role":m.role,"content":m.content} for m in rows]

def answer_in_conversation(
    db:Session,
    user:User,
    question:str,
    conversation_id:int|None=None,
    temperature:float=0.7
)->tuple[Conversation,Message]:
    question=(question or "").strip()
    if not question:
        raise BadRequestError("问题不能为空")

    if conversation_id is None:
        conv=Conversation(user_id=user.id,title=make_title(question))
        db.add(conv)
        db.flush()
    else:
        conv=db.get(Conversation,conversation_id)
        if conv is None or conv.user_id!=user.id:
            from app.core.errors import NotFoundError
            raise NotFoundError("会话不存在")

    user_msg=Message(conversation_id=conv.id,role="user",content=question)
    db.add(user_msg)
    db.flush()

    history=build_context(db,conv.id)
    messages=[{"role":"system","content":SYSTEM_PROMPT},*history]
    logger.info(
        "调用模型",extra={"extra_fields":{"conversation_id":conv.id,"ctx_msgs":len(messages)}}
    )

    answer=llm.chat(messages,temperature=temperature)

    assistant_msg=Message(conversation_id=conv.id,role="assistant",content=answer)
    db.add(assistant_msg)
    conv.updated_at=datetime.now(timezone.utc)
    db.commit()
    db.refresh(assistant_msg)
    db.refresh(conv)
    return conv,assistant_msg

def answer_once(question:str,temperature:float=0.7)->str:
    question=(question or "").strip()
    logger.info("开始处理问题，长度%d",len(question))
    if not question:
        raise BadRequestError("问题不能为空")

    messages=[
        {"role":"system","content":SYSTEM_PROMPT},
        {"role":"user","content":question},
    ]

    return llm.chat(messages,temperature=temperature)

def prepare_turn(
    db:Session,
    user:User,
    question:str,
    conversation_id:int|None=None,
)->tuple[int,list[dict]]:
    question=(question or "").strip()
    if not question:
        raise BadRequestError("问题不能为空")
    if conversation_id is None:
        conv=Conversation(user_id=user.id,title=make_title(question))
        db.add(conv)
        db.flush()
        conv_id=conv.id
    else:
        conv=db.get(Conversation,conversation_id)
        if conv is None or conv.user_id!=user.id:
            from app.core.errors import NotFoundError
            raise NotFoundError("会话不存在")
        conv_id=conv.id

    user_msg=Message(conversation_id=conv_id,role="user",content=question)
    db.add(user_msg)
    conv.updated_at=datetime.now(timezone.utc)
    db.commit()

    history=build_context(db,conv_id)
    messages=[{"role":"system","content":SYSTEM_PROMPT},*history]
    return conv_id,messages

def persist_assistant_message(conversation_id:int,content:str)->int:
    with SessionLocal() as db:
        msg=Message(conversation_id=conversation_id,role="assistant",content=content)
        db.add(msg)
        conv=db.get(Conversation,conversation_id)
        if conv is not None:
            conv.updated_at=datetime.now(timezone.utc)
        db.commit()
        db.refresh(msg)
        return msg.id
    
