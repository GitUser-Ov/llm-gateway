from app.llm import client as llm
from app.llm.prompts import SYSTEM_PROMPT
from app.core.errors import BadRequestError
import logging
logger=logging.getLogger(__name__)

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

