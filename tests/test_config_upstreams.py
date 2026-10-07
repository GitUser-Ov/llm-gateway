"""上游路由配置的解析：既支持多上游，又保证不配置时行为与旧版一致。"""
import json

import pytest

from app.core.config import get_settings


def test_defaults_to_single_deepseek_upstream(monkeypatch):
    """没配 LLM_UPSTREAMS 时，回退成单上游，老 .env 完全不用改。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_upstreams", "")

    ups = settings.upstream_list
    assert len(ups) == 1
    assert ups[0].name == "deepseek"
    assert ups[0].model == settings.llm_model
    assert settings.primary_model == settings.llm_model


def test_parses_multiple_upstreams_in_order(monkeypatch):
    """顺序即优先级。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_upstreams", json.dumps([
        {"name": "primary", "api_key": "k1", "model": "m1", "base_url": "http://a"},
        {"name": "backup", "api_key": "k2", "model": "m2", "base_url": "http://b"},
    ]))

    ups = settings.upstream_list
    assert [u.name for u in ups] == ["primary", "backup"]
    assert settings.primary_model == "m1"


def test_disabled_upstream_is_skipped(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_upstreams", json.dumps([
        {"name": "off", "enabled": False, "model": "x"},
        {"name": "on", "model": "y"},
    ]))

    ups = settings.upstream_list
    assert [u.name for u in ups] == ["on"]
    assert settings.primary_model == "y"


def test_all_disabled_falls_back_to_default(monkeypatch):
    """全被禁用时不能让上游列表变成空——否则调用层会拿不到客户端。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_upstreams", json.dumps([
        {"name": "off", "enabled": False},
    ]))

    ups = settings.upstream_list
    assert len(ups) == 1
    assert ups[0].name == "deepseek"


def test_missing_fields_inherit_defaults(monkeypatch):
    """只写关键字段也应该能跑，其余继承全局默认值。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_upstreams", json.dumps([{"api_key": "k"}]))

    up = settings.upstream_list[0]
    assert up.name == "upstream-0"
    assert up.base_url == settings.deepseek_base_url
    assert up.model == settings.llm_model
    assert up.timeout == settings.llm_timeout_seconds


def test_bad_json_raises_clear_error(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_upstreams", "{不是 JSON")

    with pytest.raises(ValueError, match="JSON"):
        settings.upstream_list


def test_non_list_json_raises(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_upstreams", json.dumps({"name": "x"}))

    with pytest.raises(ValueError, match="数组"):
        settings.upstream_list
