# Campus AI · 校园问答大模型服务后端

面向校园问答场景的 LLM 服务后端：提供带会话记忆的问答 API（含 SSE 流式输出），
并把「大模型不稳定」这件事在服务端兜住——超时、重试、降级、限流、缓存、可观测性。

**技术栈**：Python 3.12 / FastAPI / SQLAlchemy 2.0 (async) / PostgreSQL / Redis / Alembic / Docker

> **定位说明**：本项目是**服务端工程**实践，重点在稳定性、并发与可观测性。
> 当前版本**未接入检索增强（RAG）**，回答依赖模型自身知识与 System Prompt 约束，
> 因此定位为「问答服务后端」而非「校园知识库」。详见文末「已知边界」。

---

## 架构

```
                 ┌──────────────────────────────────────────┐
  浏览器 / 客户端 │  web/index.html  自研对话前端（流式渲染）  │
                 └────────────────────┬─────────────────────┘
                                      │  X-API-Key
                 ┌────────────────────▼─────────────────────┐
                 │              API 层 (FastAPI)             │
                 │  middleware: request_id + 访问日志         │
                 │  deps: 认证 / 限流 / 按天配额 / DB 会话     │
                 │  handlers: 统一异常 → 统一错误响应结构      │
                 └────────────────────┬─────────────────────┘
                                      │
                 ┌────────────────────▼─────────────────────┐
                 │            Service 层（业务编排）          │
                 │   chat_service / conversation_service     │
                 │   会话归属校验 · 上下文裁剪 · 降级 · 落库    │
                 └───────┬──────────────────────┬───────────┘
                         │                      │
        ┌────────────────▼────────┐   ┌─────────▼──────────────┐
        │   LLM 层（唯一入口）      │   │   Cache 层              │
        │   超时 · 分级重试 · 退避  │   │   精确缓存 · TTL 抖动    │
        │   流式重试安全边界        │   │   限流 · 按天配额        │
        └────────────────┬────────┘   └─────────┬──────────────┘
                         │                      │
                 DeepSeek API              Redis
                         │
                 ┌───────▼────────┐
                 │  PostgreSQL    │  user / conversations / messages
                 │  Alembic 迁移  │
                 └────────────────┘
```

---

## 核心设计

### 1. 模型调用收敛为唯一入口，重试分级

`app/llm/client.py` 是项目里**唯一**允许出现 `AsyncOpenAI(...)` 的地方。它承担三件事：

- **错误分类**：网络异常 / 429 / 5xx 才重试；401、400、402 直接失败——重试一万次结果也一样。
- **指数退避 + 随机抖动**：第 n 次重试等待 `base * 2^n + random(0, 0.5s)`。抖动的作用是避免所有客户端在同一时刻一起重试，制造新的洪峰。
- **流式重试安全边界**：流式调用只在「一个字都还没发出去」时允许重试。一旦开始输出，重试会导致回答内容重复——宁可失败，不可重复。用 `produced` 标志位控制。

SDK 自带的重试被显式关闭（`max_retries=0`），因为自己实现才能记录日志、才能控制策略。

### 2. 故障降级链路：把「报错」变成「有损可用」

上游模型不可用时，接口不返回 502，而是返回降级文案并把 `degraded` 标记透出给前端。
关键细节：**降级结果绝不写入缓存**——否则一次偶发故障会被缓存一小时，
所有用户都拿到「服务暂时不可用」。这条规则有专门的测试守住（`test_degraded_answer_not_cached`）。

### 3. 流式接口的生命周期拆分

流式响应的难点是：SSE 开始后 HTTP 状态码已经发出，**再也无法返回 4xx**。
因此把一轮问答拆成两段：

- `prepare_turn()`：在流开始前，**同步完成**所有可能失败的检查与写库（参数校验、认证、会话创建）。此时失败还能正常返回 4xx。
- `stream_generator()`：只负责生成与推送。此阶段已不在请求级的 DB 会话里（请求作用域已释放），落库改用独立的短会话 `persist_assistant_message()`。

### 4. Redis 是加速层，不是单点

缓存与限流全部 **fail-open**：Redis 故障时读缓存按未命中处理、限流直接放行，
而不是让整个服务 500。降级的是能力，不是可用性。

缓存 key = `sha256(messages + model + temperature)`，TTL 附加 ±300s 随机抖动防止集中过期。

---

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
cp .env.example .env          # 填入 DEEPSEEK_API_KEY
docker compose up -d --build   # 起 postgres + redis + app，启动时自动执行 alembic upgrade head
docker compose exec app python -m scripts.create_user 张三
```

输出中的 `sk-campus-...` 就是 API Key，**只显示这一次**。

- 对话前端：<http://localhost:8000/>
- 接口文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/v1/health>

### 方式二：本地开发

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
# 需自备 PostgreSQL 与 Redis，并修改 .env 中的 DATABASE_URL / REDIS_URL
alembic upgrade head
uvicorn app.main:app --reload
```

---

## API 一览

所有业务接口均需请求头 `X-API-Key: sk-campus-...`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/chat` | 问答（非流式），可带 `conversation_id` 续聊 |
| POST | `/api/v1/chat/stream` | 问答（SSE 流式），事件类型：`meta` / `delta` / `error` / `done` |
| POST | `/api/v1/auth/rotate-key` | 轮换 API Key（旧 Key 立即失效） |
| POST | `/api/v1/conversations` | 新建会话 |
| GET | `/api/v1/conversations` | 会话列表（分页） |
| GET | `/api/v1/conversations/{id}` | 会话详情（含消息） |
| DELETE | `/api/v1/conversations/{id}` | 删除会话（级联删除消息） |
| GET | `/api/v1/conversations/{id}/messages` | 消息列表（分页） |
| GET | `/api/v1/health` | 健康检查（无需认证） |

**统一错误响应结构**：

```json
{ "error": { "code": "rate_limited", "message": "请求过于频繁…", "request_id": "a1b2c3d4e5f6" } }
```

**限流响应头**：`X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset` / `Retry-After`

### 流式接口调用示例

```bash
curl -N http://localhost:8000/api/v1/chat/stream \
  -H "X-API-Key: sk-campus-..." \
  -H "Content-Type: application/json" \
  -d '{"question":"缓考怎么申请？","temperature":0.3}'
```

---

## 目录结构

```
app/
  api/         路由与依赖注入（health / chat / auth / conversations）
  services/    业务编排（chat_service / conversation_service）
  llm/         模型调用唯一入口 + Prompt 集中管理
  cache/       Redis 客户端 / 精确缓存 / 限流
  db/          引擎、Session、ORM 模型
  core/        配置、异常体系、异常处理器、日志、中间件、鉴权工具
  schemas/     Pydantic 出入参模型
alembic/       数据库迁移
scripts/       建用户 / 缓存量化 / 数据库连通性检查
tests/         30 个 pytest 用例
web/           自研对话前端（原生 HTML/JS，无构建步骤）
```

---

## 测试

```bash
# 需要 PostgreSQL 与 Redis（可用 docker compose up -d db redis）
# 测试库需预先存在：createdb campus_test
pytest
```

覆盖范围：认证与 Key 轮换、参数校验、会话增删查与越权访问、缓存命中与降级不缓存、
模型重试、限流与配额、SSE 事件序列。测试通过依赖注入覆盖 `get_db` / `get_redis` 实现隔离，
用 `FakeLLM` 替换模型调用，不依赖真实上游。

---

## 已知边界与后续计划

主动写明当前取舍，避免读者高估：

- **无 RAG**：回答依赖模型自身知识。校园规定这类严肃场景应改为「检索 + 强制引用」。这是下一步的首要方向（pgvector + 文档切片 + 来源返回）。
- **缓存命中率天然偏低**：缓存 key 包含完整消息历史，多轮对话每一轮的 key 都不同，实际只有「不同用户问同一句话的第一轮」能命中。这是精确缓存的固有代价，换来的是零误命中；后续可评估语义缓存。
- **System Prompt 未做注入防护**：当前没有「只依据给定资料作答」的硬约束，也没有对用户输入做越狱过滤。
- **限流为固定窗口**：实现简单、成本低，但在窗口边界存在最多 2 倍突刺。后续可换滑动窗口或令牌桶。
- **无评测集**：目前只有单元/接口测试，没有对回答质量的量化评测。

---

## License

个人学习项目，未指定开源协议。
