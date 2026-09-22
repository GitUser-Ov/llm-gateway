from pydantic import BaseModel,Field

class ChatRequest(BaseModel):
    question:str=Field(
    ...,
    min_length=1,
    max_length=2000,
    description="用户的问题",
    examples=["缓考怎么申请"],
    )
    conversation_id:int|None=Field(None,description="会话id：不传则自动新建一个会话")
    temperature:float=Field(
    0.7,
    ge=0.0,
    le=2.0,
    description="创造性，0最保守，2最发散"
    )

class ChatResponse(BaseModel):
    conversation_id:int=Field(description="所属会话id")
    message_id:int=Field(description="本次助手消息的id")
    answer:str=Field(description="模型的回答")
    model:str=Field(description="实际使用的模型名")


