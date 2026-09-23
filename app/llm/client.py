

from openai import OpenAI
from app.core.config import get_settings

settings=get_settings()

_client:OpenAI|None=None

def get_client()->OpenAI:
    global _client
    if _client is None:
        _client=OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.llm_timeout_seconds,
        )
    return _client

def chat(messages:list[dict],temperature:float=0.7)->str:
    resp=get_client().chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
    )

    return resp.choices[0].message.content or ""

def stream_chat(messages:list[dict],temperature:float=0.7):
    stream=get_client().chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        stream=True
    )

    for chunk in stream:
        if not chunk.choices:
            continue
        delta=chunk.choices[0].delta
        content=getattr(delta,"content",None)
        if content:
            yield content

            