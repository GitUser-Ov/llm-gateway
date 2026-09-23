"""大模型调用的唯一入口（异步版）。
这个文件承担了三件"稳定性"的事：
  1. 超时 —— 不让一个请求无限期挂着
  2. 重试 —— 只对"瞬时故障"重试，指数退避 + 随机抖动
  3. 错误归一 —— 把各种 SDK 异常统一成我们的 UpstreamError 系列
铁律：项目里除了这个文件，任何地方都不许出现 AsyncOpenAI(...)。
"""
import asyncio
import logging
import random
from collections.abc import AsyncIterator
import openai
from openai import AsyncOpenAI
from app.core.config import get_settings
from app.core.errors import LLMTimeoutError, UpstreamError

logger = logging.getLogger(__name__)
settings = get_settings()

_client: AsyncOpenAI | None = None

def get_client() -> AsyncOpenAI:
    """懒加载：第一次真正要用的时候才创建客户端。"""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,          # ★ 关掉 SDK 自带的重试，我们自己做（这样才能记日志、才能控制策略）
        )
    return _client

def _is_retryable(exc: Exception) -> bool:
    """判断这个错误值不值得重试。
    值得重试：网络问题、超时、429、5xx（都是"临时的"）
    不值得重试：401/400/402 等（重试一万次结果也一样）
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

async def chat(messages: list[dict], temperature: float = 0.7) -> str:
    """非流式调用：带超时、重试、退避。失败时抛 UpstreamError 的子类。"""
    last_exc: Exception | None = None
    for attempt in range(settings.llm_max_retries + 1):
        try:
            resp = await _client_or_raise().chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            last_exc = exc
            retryable = _is_retryable(exc)
            is_last = attempt >= settings.llm_max_retries
            if not retryable or is_last:
                logger.error(
                    "模型调用失败（不再重试）",
                    extra={"extra_fields": {"attempt": attempt + 1, "retryable": retryable, "err": repr(exc)}},
                )
                break
            delay = _backoff_delay(attempt)
            logger.warning(
                "模型调用失败，%.2fs 后重试",
                delay,
                extra={"extra_fields": {"attempt": attempt + 1, "err": repr(exc)}},
            )
            await asyncio.sleep(delay)
    # 统一成我们自己的异常类型，让上层不用认识 openai 的异常
    if isinstance(last_exc, openai.APITimeoutError):
        raise LLMTimeoutError() from last_exc
    raise UpstreamError("模型服务不可用") from last_exc

async def stream_chat(
    messages: list[dict], temperature: float = 0.7
) -> AsyncIterator[str]:
    """流式调用：逐段 yield 文本增量。
    ★ 重试规则和上面不同：只有在"一个字都还没发出去"的时候才允许重试。
      一旦开始输出，重试会导致内容重复。
    """
    last_exc: Exception | None = None
    for attempt in range(settings.llm_max_retries + 1):
        produced = False          # 这一轮有没有吐出过内容
        try:
            stream = await _client_or_raise().chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=temperature,
                stream=True,
            )
            async for chunk in stream:
                # ★ 有些 chunk 的 choices 是空数组（比如带 usage 统计的收尾块），不判断会 IndexError
                if not chunk.choices:
                    continue
                content = getattr(chunk.choices[0].delta, "content", None)
                if content:
                    produced = True
                    yield content
            return
        except Exception as exc:
            last_exc = exc
            if produced:
                # 已经吐过字了，重试会造成重复，只能认输
                logger.exception("流式输出中途失败，不再重试")
                raise UpstreamError("模型输出中断") from exc
            if attempt >= settings.llm_max_retries or not _is_retryable(exc):
                logger.error(
                    "流式调用失败（不再重试）",
                    extra={"extra_fields": {"attempt": attempt + 1, "err": repr(exc)}},
                )
                break
            delay = _backoff_delay(attempt)
            logger.warning("流式调用失败，%.2fs 后重试", delay)
            await asyncio.sleep(delay)
    if isinstance(last_exc, openai.APITimeoutError):
        raise LLMTimeoutError() from last_exc
    raise UpstreamError("模型服务不可用") from last_exc

def _client_or_raise() -> AsyncOpenAI:
    """取客户端；如果 Key 没配，给出明确错误而不是让 SDK 抛一个难懂的异常。"""
    if not settings.deepseek_api_key:
        raise UpstreamError("未配置 DEEPSEEK_API_KEY")
    return get_client()