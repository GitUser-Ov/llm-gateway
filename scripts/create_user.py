import argparse
import sys

from sqlalchemy import select

from app.core.security import generate_api_key,hash_api_key
from app.db.base import SessionLocal
from app.db.models import User

def main()->None:
    parser=argparse.ArgumentParser(description="创建用户")
    parser.add_argument("username",help="用户名,例如 张三")
    args=parser.parse_args()

    raw_key=generate_api_key()

    with SessionLocal() as db:
        exists=db.scalar(select(User).where(User.username==args.username))
        if exists is not None:
            print(f"用户{args,username}已存在(id={exists.id}),不再创建。")
            sys.exit(1)

        user=User(username=args.username,api_key_hash=hash_api_key(raw_key))
        db.add(user)
        db.commit()
        db.refresh(user)

    print("="*60)
    print(f"用户创建成功：{args.username}(id={user.id})")
    print()
    print(f"{raw_key}")
    print()
    print("="*60)

if __name__=="__main__":
    main()