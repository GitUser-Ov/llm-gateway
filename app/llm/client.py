"""大模型调用的唯一入口（异步版，多上游网关）。

这个文件承担四件"稳定性 + 治理"的事：
  1. 多上游路由 —— 按配置顺序选主上游，失败自动切换到下一个
  2. 超时      —— 不让一个请求无限期挂着
  3. 重试      —— 只对"瞬时故障"重试，指数退避 + 随机抖动
  4. 用量计量  —— 记录 prompt/completion token，供成本统计
铁律：项目里除了这个文件，任何地方都不许出现 AsyncOpenAI(...)。
"""
import asyncio
import logging
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass

import openai
from openai import AsyncOpenAI

from app.core.config import Upstream, get_settings
from app.core.errors import LLMTimeoutError, UpstreamError

logger = logging.getLogger(__name__)
settings = get_settings()

# 每个上游一个客户端，按上游名缓存（懒加载）
_clients: dict[str, AsyncOpenAI] = {}


@dataclass
class Usage:
    """一次调用的用量。

    由调用方创建后传入 chat / stream_chat，函数内部就地累加。
    这样做而不是用全局变量，是因为它是"一次请求"的私有数据，
    用可变对象传递在并发下天然安全。
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    upstream: str = ""
    attempts: int = 0
    fallbacks: int = 0          # 切换过几次上游

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record(self, raw, upstream: Upstream, attempts: int) -> None:
        """把 SDK 返回的 usage 落到本对象上。raw 可能是 None（部分上游不返回）。"""
        self.model = upstream.model
        self.upstream = upstream.name
        self.attempts = attempts
        if raw is None:
            return
        self.prompt_tokens = int(getattr(raw, "prompt_tokens", 0) or 0)
        self.completion_tokens = int(getattr(raw, "completion_tokens", 0) or 0)


def get_client(provider: Upstream) -> AsyncOpenAI:
    """取某个上游的客户端（懒加载，按上游名缓存）。"""
    client = _clients.get(provider.name)
    if client is None:
        client = AsyncOpenAI(
            api_key=provider.api_key,
            base_url=provider.base_url,
            timeout=provider.timeout,
            max_retries=0,          # ★ 关掉 SDK 自带的重试，我们自己做（这样才能记日志、才能控制策略）
        )
        _clients[provider.name] = client
    return client


def _client_or_raise(provider: Upstream | None = None) -> AsyncOpenAI:
    """取客户端；Key 没配就给出明确错误，而不是让 SDK 抛一个难懂的异常。

    不传 provider 时使用主上游——保留这个默认值是为了向后兼容。
    """
    p = provider or settings.upstream_list[0]
    if not p.api_key:
        raise UpstreamError(f"上游 {p.name} 未配置 API Key")
    return get_client(p)


def _is_retryable(exc: Exception) -> bool:
    """判断这个错误值不值得在**同一个上游**上重试。

    值得重试：网络问题、超时、429、5xx（都是"临时的"）
    不值得重试：401/400/402 等（重试一万次结果也一样）
    注意：不值得重试 ≠ 不值得切换上游。认证失败在主上游上重试无意义，
          但换一个有 Key 的上游很可能就成功了，所以上层仍会继续 failover。
    """
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, openai.RateLimitError):          # 429
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code >= 500
    return False


def _backoff_delay(attempt: int) -> float:
    """指数退避 + 抖动。

    第 0 次重试等 ~0.5s，第 1 次 ~1.0s，第 2 次 ~2.0s，再叠加 0~0.5s 的随机抖动。
    抖动的作用：让所有客户端不要在同一时刻一起重试（避免制造新的洪峰）。
    """
    return settings.llm_retry_base_delay * (2 ** attempt) + random.uniform(0, 0.5)


def _raise_last(last_exc: Exception | None, tried: int) -> None:
    """把所有上游都试完之后，统一成我们自己的异常类型。"""
    if isinstance(last_exc, openai.APITimeoutError):
        raise LLMTimeoutError() from last_exc
    raise UpstreamError(f"模型服务不可用（已尝试 {tried} 个上游）") from last_exc


async def chat(
    messages: list[dict],
    temperature: float = 0.7,
    usage: Usage | None = None,
) -> str:
    """非流式调用：带多上游故障转移、超时、重试、退避，并记录用量。

    外层循环遍历上游（故障转移），内层循环是同一上游上的重试。
    任意一个上游成功即返回；全部失败才抛 UpstreamError 的子类。
    """
    providers = settings.upstream_list
    last_exc: Exception | None = None

    for index, provider in enumerate(providers):
        for attempt in range(settings.llm_max_retries + 1):
            try:
                resp = await _client_or_raise(provider).chat.completions.create(
                    model=provider.model,
                    messages=messages,
                    temperature=temperature,
                )
                content = resp.choices[0].message.content or ""
                if usage is not None:
                    usage.record(getattr(resp, "usage", None), provider, attempt + 1)
                    usage.fallbacks = index
                return content
            except Exception as exc:
                last_exc = exc
                retryable = _is_retryable(exc)
                is_last_try = attempt >= settings.llm_max_retries
                if not retryable or is_last_try:
                    logger.error(
                        "上游调用失败，准备切换下一个上游",
                        extra={"extra_fields": {
                            "upstream": provider.name,
                            "attempt": attempt + 1,
                            "retryable": retryable,
                            "has_next": index + 1 < len(providers),
                            "err": repr(exc),
                        }},
                    )
                    break                       # 跳出重试，去试下一个上游
                delay = _backoff_delay(attempt)
                logger.warning(
                    "上游调用失败，%.2fs 后重试",
                    delay,
                    extra={"extra_fields": {
                        "upstream": provider.name,
                        "attempt": attempt + 1,
                        "err": repr(exc),
                    }},
                )
                await asyncio.sleep(delay)

    _raise_last(last_exc, len(providers))


async def stream_chat(
    messages: list[dict],
    temperature: float = 0.7,
    usage: Usage | None = None,
) -> AsyncIterator[str]:
    """流式调用：逐段 yield 文本增量，同样支持多上游故障转移。

    ★ 两条铁律：
      1. 只有在"一个字都还没发出去"的时候才允许重试或切换上游。
         一旦开始输出，重试会让用户看到重复内容，切换上游会让前后文不一致。
      2. 有些 chunk 的 choices 是空数组（带 usage 统计的收尾块），不判断会 IndexError。
    """
    providers = settings.upstream_list
    last_exc: Exception | None = None
    extra = {"stream_options": {"include_usage": True}} if settings.llm_stream_usage else {}

    for index, provider in enumerate(providers):
        produced = False          # 这个上游有没有吐出过内容
        for attempt in range(settings.llm_max_retries + 1):
            try:
                stream = await _client_or_raise(provider).chat.completions.create(
                    model=provider.model,
                    messages=messages,
                    temperature=temperature,
                    stream=True,
                    **extra,
                )
                async for chunk in stream:
                    if not chunk.choices:
                        # 收尾块：只有 usage，没有内容
                        if usage is not None:
                            usage.record(getattr(chunk, "usage", None), provider, attempt + 1)
                            usage.fallbacks = index
                        continue
                    content = getattr(chunk.choices[0].delta, "content", None)
                    if content:
                        produced = True
                        yield content
                return
            except Exception as exc:
                last_exc = exc
                if produced:
                    # 已经吐过字了，重试会造成重复、切换上游会造成前后矛盾，只能认输
                    logger.exception(
                        "流式输出中途失败，不再重试也不再切换上游",
                        extra={"extra_fields": {"upstream": provider.name}},
                    )
                    raise UpstreamError("模型输出中断") from exc
                if attempt >= settings.llm_max_retries or not _is_retryable(exc):
                    logger.error(
                        "上游流式调用失败，准备切换下一个上游",
                        extra={"extra_fields": {
                            "upstream": provider.name,
                            "attempt": attempt + 1,
                            "has_next": index + 1 < len(providers),
                            "err": repr(exc),
                        }},
                    )
                    break
                delay = _backoff_delay(attempt)
                logger.warning(
                    "上游流式调用失败，%.2fs 后重试",
                    delay,
                    extra={"extra_fields": {"upstream": provider.name, "attempt": attempt + 1}},
                )
                await asyncio.sleep(delay)

    _raise_last(last_exc, len(providers))
