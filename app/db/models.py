from datetime import datetime
from sqlalchemy import(
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    )

from sqlalchemy.orm import Mapped,mapped_column,relationship

from app.db.base import Base

class User(Base):
    __tablename__="user"

    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    username:Mapped[str]=mapped_column(String(64),unique=True,index=True)
    api_key_hash:Mapped[str]=mapped_column(String(128),unique=True,index=True)
    is_active:Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)
    created_at:Mapped[datetime]=mapped_column(
        DateTime(timezone=True),server_default=func.now(),nullable=False
        )
    
    conversations:Mapped[list["Conversation"]]=relationship(
        back_populates="user",
        cascade="all,delete-orphan"
    )

    def __repr__(self)->str:
        return f"<User id={self.id} username={self.username}>"

class Conversation(Base):
    __tablename__="conversations"

    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    user_id:Mapped[int]=mapped_column(
        ForeignKey("user.id",ondelete="CASCADE"),index=True,nullable=False
        )

    title:Mapped[str]=mapped_column(String(200),default="新对话",nullable=False)
    digest:Mapped[str]=mapped_column(String(500),default="",nullable=False)
    created_at:Mapped[datetime]=mapped_column(
        DateTime(timezone=True),server_default=func.now(),nullable=False
        )
    updated_at:Mapped[datetime]=mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    user:Mapped["User"]=relationship(back_populates="conversations")
    messages:Mapped[list["Message"]]=relationship(
        back_populates="conversation",
        cascade="all,delete-orphan",
        order_by="Message.id"
        )

    def __repr__(self)->str:
        return f"<Conversation id={self.id} title={self.title!r}>"

class Message(Base):
    __tablename__="messages"
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    conversation_id:Mapped[int]=mapped_column(
        ForeignKey("conversations.id",ondelete="CASCADE"),nullable=False
        )
    role:Mapped[str]=mapped_column(String(16),nullable=False)
    content:Mapped[str]=mapped_column(Text,nullable=False)
    prompt_tokens:Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    completion_tokens:Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    created_at:Mapped[datetime]=mapped_column(
        DateTime(timezone=True),server_default=func.now(),nullable=False
        )

    conversation:Mapped["Conversation"]=relationship(back_populates="messages")

    __table_args__=(
        Index("ix_messages__conversation_id_id","conversation_id","id"),
    )

    def __repr__(self)->str:
        return f"<Message id={self.id} role={self.role} len={len(self.content)}>"