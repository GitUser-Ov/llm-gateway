"""多上游网关的核心行为：故障转移、用量计量、以及流式的「不可转移」边界。

这些用例不碰真实网络——通过替换 _client_or_raise 注入假 SDK 客户端，
用 LLM_UPSTREAMS 配置出多个上游，逐个观察路由结果。
"""
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

import app.llm.client as llm_client
from app.core.config import get_settings
from app.core.errors import UpstreamError

MESSAGES = [{"role": "user", "content": "你好"}]


def _conn_error() -> Exception:
    """一个典型的瞬时网络故障（可重试）。"""
    return openai.APIConnectionError(request=httpx.Request("POST", "http://test"))


def _auth_error() -> Exception:
    """一个典型的永久故障（不可重试）——但仍应触发切换上游。"""
    resp = httpx.Response(401, request=httpx.Request("POST", "http://test"))
    return openai.AuthenticationError("bad key", response=resp, body=None)


def _set_upstreams(monkeypatch, names: list[str], max_retries: int = 0) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "llm_upstreams",
        json.dumps([
            {"name": n, "api_key": f"key-{n}", "model": f"model-{n}", "base_url": "http://test"}
            for n in names
        ]),
    )
    monkeypatch.setattr(settings, "llm_max_retries", max_retries)


def _chat_client(fail: dict[str, Exception], content_of=None):
    """构造一个假的 SDK 客户端：某些上游按名字抛错，其余正常返回。"""
    def _client_or_raise(provider=None):
        name = provider.name

        async def create(**kwargs):
            if name in fail:
                raise fail[name]
            text = content_of(provider) if content_of else f"来自{name}"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7),
            )

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    return _client_or_raise


def _stream_client(fail_after: dict[str, int]):
    """fail_after[name] = 该上游吐几个字符后抛连接错误。

    不在字典里的上游视为健康，会完整吐完内容——故障转移的目标上游就该这样。
    """
    remaining = dict(fail_after)

    def _client_or_raise(provider=None):
        name = provider.name

        async def create(**kwargs):
            async def gen():
                left = remaining.get(name)
                for ch in "上游内容":
                    if left is not None and left <= 0:
                        raise _conn_error()
                    if left is not None:
                        left -= 1
                        remaining[name] = left
                    yield SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content=ch))]
                    )

            return gen()

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    return _client_or_raise


async def test_failover_to_second_upstream(monkeypatch):
    _set_upstreams(monkeypatch, ["a", "b"])
    monkeypatch.setattr(llm_client, "_client_or_raise", _chat_client({"a": _conn_error()}))

    usage = llm_client.Usage()
    answer = await llm_client.chat(MESSAGES, temperature=0.3, usage=usage)

    assert answer == "来自b"
    assert usage.upstream == "b"
    assert usage.model == "model-b"
    assert usage.fallbacks == 1


async def test_auth_error_also_fails_over(monkeypatch):
    """不可重试 ≠ 不切换上游：主上游 Key 失效时，换一个上游往往就好了。"""
    _set_upstreams(monkeypatch, ["a", "b"])
    calls: list[str] = []

    def _client(provider=None):
        calls.append(provider.name)

        async def create(**kwargs):
            if provider.name == "a":
                raise _auth_error()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="B 成功了"))],
                usage=None,
            )

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(llm_client, "_client_or_raise", _client)

    answer = await llm_client.chat(MESSAGES, temperature=0.3)
    assert answer == "B 成功了"
    assert calls == ["a", "b"]          # 401 不在 a 上重试，直接换 b


async def test_all_upstreams_fail_raises_upstream_error(monkeypatch):
    _set_upstreams(monkeypatch, ["a", "b"])
    monkeypatch.setattr(
        llm_client, "_client_or_raise",
        _chat_client({"a": _conn_error(), "b": _conn_error()}),
    )

    with pytest.raises(UpstreamError):
        await llm_client.chat(MESSAGES, temperature=0.3)


async def test_retry_happens_on_same_upstream_before_failover(monkeypatch):
    """同一个上游上先重试到上限，才轮到下一个上游。"""
    _set_upstreams(monkeypatch, ["a", "b"], max_retries=2)
    attempts: list[str] = []

    def _client(provider=None):
        async def create(**kwargs):
            attempts.append(provider.name)
            if provider.name == "a":
                raise _conn_error()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="B"))],
                usage=None,
            )

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(llm_client, "_client_or_raise", _client)
    monkeypatch.setattr(llm_client, "_backoff_delay", lambda attempt: 0.0)   # 测试里不等真时间

    await llm_client.chat(MESSAGES, temperature=0.3)
    assert attempts == ["a", "a", "a", "b"]     # a 重试 3 次，然后换 b


async def test_stream_fails_over_when_nothing_produced(monkeypatch):
    """一个字都还没发出去 → 允许切换上游。"""
    _set_upstreams(monkeypatch, ["a", "b"])
    monkeypatch.setattr(
        llm_client, "_client_or_raise", _stream_client({"a": 0})
    )

    chunks = [c async for c in llm_client.stream_chat(MESSAGES, temperature=0.3)]
    assert "".join(chunks) == "上游内容"


async def test_stream_does_not_failover_after_producing(monkeypatch):
    """★ 已经吐出内容 → 绝不重试也绝不换上游，宁可报错也不能让用户看到重复/错乱的内容。"""
    _set_upstreams(monkeypatch, ["a", "b"], max_retries=2)
    monkeypatch.setattr(
        llm_client, "_client_or_raise", _stream_client({"a": 1})
    )
    monkeypatch.setattr(llm_client, "_backoff_delay", lambda attempt: 0.0)

    chunks: list[str] = []
    with pytest.raises(UpstreamError):
        async for c in llm_client.stream_chat(MESSAGES, temperature=0.3):
            chunks.append(c)

    assert chunks == ["上"]        # 只拿到第一个字就断了，不会出现 B 上游的内容


async def test_stream_usage_recorded(monkeypatch):
    """流式收尾块只有 usage 没有 choices，必须被正确识别并记账。"""
    _set_upstreams(monkeypatch, ["a"])

    def _client(provider=None):
        async def create(**kwargs):
            async def gen():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="答"))]
                )
                # 收尾块：choices 为空，只带 usage
                yield SimpleNamespace(
                    choices=[],
                    usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
                )

            return gen()

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(llm_client, "_client_or_raise", _client)

    usage = llm_client.Usage()
    chunks = [c async for c in llm_client.stream_chat(MESSAGES, temperature=0.3, usage=usage)]

    assert "".join(chunks) == "答"
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 20
    assert usage.total_tokens == 120
