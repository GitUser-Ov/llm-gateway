from app.llm import client as llm
from app.llm.prompts import SYSTEM_PROMPT
from app.core.errors import BadRequestError

def answer_once(question:str,temperature:float=0.7)->str:
    raise BadRequestError("我自己的业务错误")
    question=(question or "").strip()
    if not question:
        raise BadRequestError("问题不能为空")

    messages=[
        {"role":"system","content":SYSTEM_PROMPT},
        {"role":"user","content":question},
    ]

    return llm.chat(messages,temperature=temperature)

