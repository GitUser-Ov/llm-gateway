import hashlib

from sqlalchemy import select

from app.db.base import Base,SessionLocal,engine
from app.db.models import Conversation,Message,User

def hash_api_key(raw:str)->str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def main()->None:
    #Base.metadata.drop_all(bind=engine)
    #Base.metadata.create_all(bind=engine)
    print("1建表完成")

    with SessionLocal() as db:
        user=User(username="张三",api_key_hash=hash_api_key("sk-test-123"))
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"2用户写入成功 id={user.id}")

        conv=Conversation(user_id=user.id,title="缓考咨询")
        db.add(conv)
        db.commit()
        db.refresh(conv)

        db.add_all(
        [Message(conversation_id=conv.id,role="user",content="缓考怎么申请？"),
         Message(conversation_id=conv.id,role="assistant",content="请考前3个工作日提交申请"),
        ]
        )
        db.commit()
        print(f"3会话id={conv.id}与两条消息写入成功")

        stmt=(
            select(Message)
            .where(Message.conversation_id==conv.id)
            .order_by(Message.id)
            )

        rows=db.scalars(stmt).all()
        print("4读回消息:")

        for m in rows:
            print(f"[{m.id}]{m.role}:{m.content}")

        print(f"5反向导航：会话标题={rows[0].conversation.title}")
        print(f"用户的所有会话数={len(user.conversations)}")

if __name__=="__main__":
    main()


