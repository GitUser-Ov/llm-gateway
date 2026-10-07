# Campus AI · 大模型接入网关

把「调用大模型」这件事从每个业务里抽出来，做成一层可复用的服务：**多上游路由与故障转移**、
超时与分级重试、模型故障降级、多租户限流与配额、答案缓存、用量计量与调用审计、
**私有知识库检索（RAG）**。

业务侧（校园问答）作为**示例租户**跑在这层网关之上。

**技术栈**：Python 3.12 / FastAPI / SQLAlchemy 2.0 (async) / PostgreSQL + pgvector / Redis / Alembic / sentence-transformers / Docker

---

## 实测数据

以下数字均在本机 `docker compose` 全栈环境下实测（脚本见 `scripts/`）：

| 场景 | 结果 |
|---|---|
| **上游故障转移**：主上游完全不可用（连接被拒） | 成功率 **3/3**，请求自动切到备用上游，用户侧无感 |
| **答案缓存**：冷启动首次调用 → 命中缓存 | **1.92s → 0.016s，加速约 123×** |
| **10 个并发**请求同一问题 | 总耗时 **95ms**，缓存命中 **10/10** |
| **知识库检索**（12 个真实问题，语料为项目自身文档） | top-4 命中率 **10/12 = 83%**；补全文档后复测预期提升，见「已知边界」 |
| **向量模型选型**：中文语料 7 题 | bge-small-zh **100%** vs nomic-embed-text **57%** |
| **真实调用用量计量** | `prompt_tokens=79 / completion_tokens=150`，落库并随响应返回 |
| **测试覆盖** | **62 个用例全通过**，`app/llm/client.py` 覆盖率由 43% → **85%**（全项目 82%） |

> 复现：`python -m scripts.measure_cache <KEY>`、`python -m scripts.bench_failover <KEY>`、
> `python -m scripts.eval_rag`

---

## 架构

```
                 ┌──────────────────────────────────────────┐
  浏览器 / 客户端 │  web/index.html  自研对话前端（流式+引用） │
                 └────────────────────┬─────────────────────┘
                                      │  X-API-Key（租户身份）
                 ┌────────────────────▼─────────────────────┐
                 │              API 层 (FastAPI)             │
                 │  middleware: request_id + 访问日志         │
                 │  deps: 认证 / 限流 / 按天配额 / DB 会话     │
                 │  handlers: 统一异常 → 统一错误响应结构      │
                 └────────────────────┬─────────────────────┘
                                      │
                 ┌────────────────────▼─────────────────────┐
                 │            Service 层（业务编排）          │
                 │  检索 → 拼上下文 → 调模型 → 落库            │
                 │  会话归属校验 · 降级 · 无依据拒答            │
                 └──┬──────────────┬──────────────┬─────────┘
                    │              │              │
        ┌───────────▼───┐ ┌────────▼──────┐ ┌─────▼──────────────┐
        │ RAG 检索层     │ │ LLM 网关       │ │ Cache / Quota 层    │
        │ 切片·向量化    │ │ ★ 多上游路由   │ │ 精确缓存 · 限流      │
        │ pgvector 检索  │ │ 分级重试·降级  │ │ 按天配额 · fail-open │
        │ 引用来源       │ │ 用量计量      │ │                     │
        └───────┬───────┘ └──┬────────┬───┘ └─────────┬───────────┘
                │            │        │               │
        ┌───────▼──────┐ ┌───▼───┐ ┌──▼────────┐      │
        │ bge-small-zh │ │主上游 │ │备用上游    │   Redis
        │ (本地向量模型)│ │(优先) │ │(自动接管) │
        └───────┬──────┘ └───────┘ └───────────┘      │
                │                                     │
        ┌───────▼─────────────────────────────────────▼──────┐
        │  PostgreSQL + pgvector                              │
        │  user / conversations / messages（token 计量）        │
        │  documents / chunks（embedding vector(512) + HNSW）  │
        └─────────────────────────────────────────────────────┘
```

---

## 核心设计

### 1. 多上游路由与故障转移

`app/llm/client.py` 是项目里**唯一**允许出现 `AsyncOpenAI(...)` 的地方。上游通过 `LLM_UPSTREAMS`
配置（JSON 数组，**顺序即优先级**），未配置时自动回退为单上游，老 `.env` 无需改动：

```bash
LLM_UPSTREAMS=[{"name":"deepseek","api_key":"sk-xxx","model":"deepseek-chat"},
               {"name":"doubao","api_key":"ark-xxx",
                "base_url":"https://ark.cn-beijing.volces.com/api/v3","model":"doubao-xxx"}]
```

**双层循环**：外层遍历上游做故障转移，内层在同一上游上做重试。任意一个成功即返回。

关键判断：「**不可重试 ≠ 不可切换上游**」。401（Key 失效）在主上游上重试一万次都没用，
但换一个有 Key 的上游立刻就成功了——所以认证失败会直接触发切换，而不是直接报错。

### 2. 流式重试的安全边界（最容易被忽略的一点）

流式调用只在「**一个字都还没发出去**」时允许重试或切换上游。一旦开始输出：

- 重试 → 用户会看到两遍开头；
- 切换上游 → 前后文来自不同模型，语义可能出现矛盾。

所以此时宁可报 `模型输出中断`，也不能"聪明地"重试。用 `produced` 标志位守住这条线。
另有一个容易踩的坑：部分收尾 chunk 的 `choices` 是空数组（只带 usage），不判断会 `IndexError`。

### 3. RAG：私有知识库检索

**向量模型选型（有实测依据）**：默认 `BAAI/bge-small-zh-v1.5`。

做选型时拿 7 条真实中文教务文本做过对比：通用英文模型 `nomic-embed-text`（768 维）
top-1 命中率只有 **57%**，典型错例是把「挂科了什么时候重考」检索到「图书馆开放时间」；
而面向中文训练的 `bge-small-zh-v1.5`（512 维）是 **100%**。所以中文场景默认用后者，
尽管它的维度更小、体积只有 91MB。

**可插拔的向量后端**（`app/rag/embedder.py`）：`local`（sentence-transformers）/ `ollama` / `fake`（测试用）。
业务代码只依赖 `Embedder` 抽象，换模型不改业务逻辑，测试也不必依赖 torch。

**三层切片策略**（`app/rag/chunker.py`）：按 Markdown 标题分节 → 段落聚合到目标长度 →
超长块用带重叠的定长窗口兜底。重叠的作用是让跨边界的答案至少在某一块里完整。

**检索**（`app/rag/service.py`）走 pgvector 的**余弦距离**（`<=>`）配合 **HNSW 索引**，
`score = 1 - distance`，低于 `RAG_MIN_SCORE` 的结果会被丢弃。灌入以文件路径为 `source`
做**幂等替换**，同一份文档重复灌入不会产生重复切片。

**强制引用 + 无依据拒答**：RAG 的 system prompt 规定「只能依据参考资料作答，
每个结论用 [编号] 标注来源，资料里没有的必须说无法回答」。检索为空时**直接拒答，不调模型**——
既省 token，也避免模型自由发挥产生幻觉。引用来源会随 `citations` 字段返回，
流式场景则在首个 `meta` 事件里就推给前端。

### 4. 错误分级与指数退避

- **值得重试**：网络异常、超时、429、5xx（都是"临时的"）
- **不值得重试**：401 / 400 / 402（结果不会变）
- **退避公式**：`base * 2^attempt + random(0, 0.5s)`，抖动用于避免所有客户端在同一时刻集体重试，制造新的洪峰。

SDK 自带重试被显式关闭（`max_retries=0`），因为自己实现才能记录日志、才能控制策略。

### 5. 故障降级链路：把「报错」变成「有损可用」

所有上游都不可用时，接口不返回 502，而是返回降级文案并把 `degraded` 标记透出。
关键细节：**降级结果绝不写入缓存**——否则一次偶发故障会被缓存一小时，
所有用户都拿到「服务暂时不可用」。这条规则有专门的测试守住（`test_degraded_answer_not_cached`）。

### 6. 用量计量与调用审计

每次模型调用的 `prompt_tokens` / `completion_tokens` 落库到消息记录，并随响应/SSE `done` 事件返回。
缓存命中与降级时为 0（**没有真实调用就不记账**）。流式通过 `stream_options.include_usage`
向模型索要收尾用量块。据此可以回答"这个租户这个月花了多少 token"。

### 7. 流式接口的生命周期拆分

SSE 开始后 HTTP 状态码已发出，**再也无法返回 4xx**。因此把一轮问答拆成两段：

- `prepare_turn()`：流开始前**同步完成**所有可能失败的检查、检索与写库（参数校验、认证、会话创建）。
  检索放在这里也是刻意的——检索需要数据库会话，而流开始后请求级会话已释放。
- `stream_generator()`：只负责生成与推送；此阶段不再持有请求级 DB 会话，落库改用独立短会话 `persist_assistant_message()`。

### 8. Redis 是加速层，不是单点

缓存与限流全部 **fail-open**：Redis 故障时读缓存按未命中处理、限流直接放行，
而不是让整个服务 500。降级的是能力，不是可用性。

缓存 key = `sha256(messages + model + temperature)`，TTL 附加 ±300s 随机抖动防止集中过期。
注意 RAG 场景下 `messages` 里已经包含检索到的资料，因此知识库更新后缓存 key 自然失效，不会返回过期答案。

---

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
cp .env.example .env          # 填入 DEEPSEEK_API_KEY
docker compose up -d --build   # 起 postgres(pgvector) + redis + app，自动执行 alembic upgrade head
docker compose exec app python -m scripts.create_user 张三
docker compose exec app python -m scripts.ingest /app/docs   # 灌入知识库（可选）
```

输出中的 `sk-campus-...` 就是 API Key，**只显示这一次**。

- 对话前端：<http://localhost:8000/>
- 接口文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/v1/health>

> **关于向量模型**：默认用本地 `BAAI/bge-small-zh-v1.5`（约 92MB）。
> compose 里已把宿主机的 HuggingFace 缓存只读挂载进容器，避免每次重建都重新下载。
> 首次运行会自动下载模型；若想完全离线，先在本机跑一次 `ingest` 把模型缓存下来。

### 方式二：本地开发

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
# 需自备 PostgreSQL（含 pgvector 扩展）与 Redis，并修改 .env 中的 DATABASE_URL / REDIS_URL
alembic upgrade head
python -m scripts.create_user 张三
python -m scripts.ingest README.md
uvicorn app.main:app --reload
```

---

## 知识库使用

```bash
# 灌入单个文件或整个目录（支持 .md / .txt，递归收集）
python -m scripts.ingest README.md
python -m scripts.ingest ./docs

# 评测检索质量（12 个真实问题；加 --full 会连模型回答一起评测，消耗 token）
python -m scripts.eval_rag
python -m scripts.eval_rag --full
```

灌入以文件路径为标识，**重复灌入同一文件是替换而非追加**，改完文档重新跑一次即可。

---

## API 一览

所有业务接口均需请求头 `X-API-Key: sk-campus-...`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/chat` | 问答（非流式），可带 `conversation_id` 续聊；先检索知识库再作答 |
| POST | `/api/v1/chat/stream` | 问答（SSE 流式），事件类型：`meta`（含 citations）/ `delta` / `error` / `done` |
| POST | `/api/v1/auth/rotate-key` | 轮换 API Key（旧 Key 立即失效） |
| POST | `/api/v1/conversations` | 新建会话 |
| GET | `/api/v1/conversations` | 会话列表（分页） |
| GET | `/api/v1/conversations/{id}` | 会话详情（含消息与 token 用量） |
| DELETE | `/api/v1/conversations/{id}` | 删除会话（级联删除消息） |
| GET | `/api/v1/conversations/{id}/messages` | 消息列表（分页） |
| GET | `/api/v1/health` | 健康检查（无需认证） |

**问答响应含计量、路由与引用信息**：

```json
{
  "conversation_id": 4, "message_id": 9,
  "answer": "……", "model": "deepseek-chat", "upstream": "deepseek",
  "from_cache": false, "degraded": false,
  "prompt_tokens": 79, "completion_tokens": 150,
  "citations": ["README.md · 3. RAG：私有知识库检索"]
}
```

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
  -d '{"question":"这个项目怎么做故障转移？","temperature":0.3}'
```

---

## 目录结构

```
app/
  api/         路由与依赖注入（health / chat / auth / conversations）
  services/    业务编排（chat_service / conversation_service）
  llm/         网关：多上游路由、重试、降级、用量计量 + Prompt 集中管理
  rag/         知识库：切片器 / 可插拔向量后端 / 检索服务
  cache/       Redis 客户端 / 精确缓存 / 限流
  db/          引擎、Session、ORM 模型（业务 + RAG）
  core/        配置、异常体系、异常处理器、日志、中间件、鉴权工具
  schemas/     Pydantic 出入参模型
alembic/       数据库迁移（含 pgvector 扩展与 HNSW 索引）
scripts/       建用户 / 灌入知识库 / 检索评测 / 缓存与故障转移基准 / 连通性检查
tests/         44 个 pytest 用例
web/           自研对话前端（原生 HTML/JS，无构建步骤）
```

---

## 测试

```bash
# 需要 PostgreSQL（含 pgvector）与 Redis（可用 docker compose up -d db redis）
# 测试库需预先存在：createdb campus_test
pytest
```

覆盖范围：**多上游故障转移**（含"认证失败也要切换上游"、流式已产出后禁止切换）、
上游列表配置解析、认证与 Key 轮换、参数校验、会话增删查与越权访问、
缓存命中与降级不缓存、同上游重试次数、用量计量落库、限流与配额、SSE 事件序列。
测试通过依赖注入覆盖 `get_db` / `get_redis` 实现隔离，用 `FakeLLM` 替换模型调用，
**向量后端用 `FakeEmbedder` 替换**，因此整条测试不依赖真实上游、也不依赖 torch。

---

## 已知边界与后续计划

主动写明当前取舍，避免读者高估：

- **故障转移是"顺序尝试"而非"健康检查"**：不做主动探活，靠失败触发切换，因此首次失败仍要付出重试退避的时间代价（实测约 4.9s）。后续可按上游维护熔断器状态，短期内连续失败直接跳过。
- **检索质量尚未做规范化评测**：`scripts/eval_rag.py` 目前用「top-k 是否包含期望关键词」这种轻量判据，
  不是标准的 Recall@k / MRR，也没有人工标注的参考答案。当前实测 **10/12**，
  两个未命中都出现在「RAG 使用方式」类问题上——原因是灌入语料时 README 还没有对应章节，
  补全文档并重新灌入后应能改善。后续应建立带标注的评测集。
- **无重排序（rerank）**：检索直接取余弦相似度 top-k，没有第二阶段的交叉编码器重排。在语料变大后，重排通常是提升精度最划算的一步。
- **切片按字符切**：中文按字计数，未接入 token 级计数，因此 `CHUNK_SIZE=500` 在不同语言下对应的 token 数并不一致。
- **缓存命中率天然偏低**：缓存 key 包含完整消息历史，多轮对话每一轮的 key 都不同，实际只有「不同用户问同一句话的第一轮」能命中。这是精确缓存的固有代价，换来的是零误命中；后续可评估语义缓存。
- **System Prompt 的注入防护有限**：虽有「只能依据资料作答」的硬约束，但没有对用户输入做越狱检测。RAG 场景下这比闲聊更危险（可能诱导模型偏离资料）。
- **知识库无权限隔离**：所有租户共享同一份知识库，没有按租户/文档设定可见范围。
- **限流为固定窗口**：实现简单、成本低，但在窗口边界存在最多 2 倍突刺。后续可换滑动窗口或令牌桶。

---

## License

个人学习项目，未指定开源协议。
