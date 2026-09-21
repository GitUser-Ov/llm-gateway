from app.llm import client as llm
from app.llm.prompts import SYSTEM_PROMPT

def answer_once(question:str,temperature:float=0.7)->str:
    question=(question or "").strip()
    if not question:
        raise ValueError("问题不能为空")

    messages=[
        {"role":"system","content":SYSTEM_PROMPT},
        {"role":"user","content":question},
    ]

    return llm.chat(messages,temperature=temperature)