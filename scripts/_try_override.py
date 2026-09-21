from fastapi.testclient import TestClient

from app.api.deps import get_current_user_lite,CurrentUserLite
from app.main import app

app.dependency_overrides[get_current_user_lite]=lambda:CurrentUserLite(name="测试用户")

client=TestClient(app)
resp=client.post("/api/v1/chat",json={"question":"你好","temperature":"0.3"})
print(resp.status_code)
print(resp.json())

