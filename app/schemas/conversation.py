from datetime import datetime
from typing import Generic,TypeVar

from pydantic import BaseModel,ConfigDict,Field

T=TypeVar("T")

class Page(BaseModel,Generic[T]):
    items:list[T]
    total:int=Field(description="总条数")
    page:int=Field(description="当前页码，从1开始")
    size:int=Field(description="每页条数")

class ConversationCreate(BaseModel):
    title:str=Field("新对话",max_length=200,description="会话标题")

class ConversationOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)

    id:int
    title:str
    message_count:int=0
    created_at:datetime
    updated_at:datetime

class MessageOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)

    id:int
    role:str=Field(description="user或assistant")
    content:str
    created_at:datetime

class ConversationDetail(ConversationOut):
    messages:list[MessageOut]=[]
    