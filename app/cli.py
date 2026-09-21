from app.services.chat_service import answer_once

if __name__=="__main__":
    question=input("你的问题：")
    print("\n回答：")
    print(answer_once(question))