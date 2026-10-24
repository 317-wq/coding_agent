"""覆盖模型 provider 配置解析与跨 provider 故障转移。"""

import os
from types import SimpleNamespace

import pytest

from app.runtime.config import (
    MAX_PROVIDERS,
    Settings,
    reject_unsupported_provider_indices,
)
from app.runtime.model import ModelAdapter, ModelFallbackExhaustedError
from app.runtime.provider import ProviderConfig, normalize_models


@pytest.fixture(autouse=True)
def _isolate_model_env(monkeypatch) -> None:
    """隔离 conftest 与开发者本机注入的模型配置变量。"""
    for name in list(os.environ):
        if name.upper().startswith(("MODEL_PROVIDER_", "QWEN_")):
            monkeypatch.delenv(name, raising=False)


def _settings(**overrides) -> Settings:
    """构造不读取 .env 的配置对象，避免依赖开发者本机环境。"""
    return Settings(_env_file=None, **overrides)


def test_explicit_providers_are_parsed_in_index_order():
    """验证多个 provider 按序号解析并保持顺序。"""
    settings = _settings(
        model_provider_1_name="primary",
        model_provider_1_base_url="https://primary/v1",
        model_provider_1_api_key="key-1",
        model_provider_1_models="m1,m2",
        model_provider_2_name="backup",
        model_provider_2_base_url="https://backup/v1",
        model_provider_2_api_key="key-2",
        model_provider_2_models="m3",
    )

    providers = settings.providers

    assert [(p.name, p.models) for p in providers] == [
        ("primary", ("m1", "m2")),
        ("backup", ("m3",)),
    ]


def test_provider_model_list_is_deduplicated_and_trimmed():
    """验证模型列表去重、去空白。"""
    settings = _settings(
        model_provider_1_base_url="https://x/v1",
        model_provider_1_api_key="key",
        model_provider_1_models=" m1, m2 , m1 ",
    )

    assert settings.providers[0].models == ("m1", "m2")


def test_legacy_qwen_config_maps_to_single_provider():
    """验证旧 QWEN_* 配置仍可用并映射为单个 provider。"""
    settings = _settings(
        qwen_api_key="key",
        qwen_base_url="https://legacy/v1",
        qwen_chat_model_1="q1",
        qwen_chat_model_2="q2",
    )

    providers = settings.providers

    assert len(providers) == 1
    assert providers[0].name == "qwen"
    assert providers[0].base_url == "https://legacy/v1"
    assert providers[0].models == ("q1", "q2")


def test_explicit_provider_wins_over_legacy_config():
    """验证新格式存在时忽略旧格式，避免两套配置混用。"""
    settings = _settings(
        model_provider_1_base_url="https://new/v1",
        model_provider_1_api_key="new-key",
        model_provider_1_models="new-model",
        qwen_api_key="legacy-key",
        qwen_base_url="https://legacy/v1",
        qwen_chat_model_1="legacy-model",
    )

    assert [p.models for p in settings.providers] == [("new-model",)]


def test_incomplete_provider_reports_missing_fields():
    """验证 provider 配置不完整时给出可操作的错误。"""
    settings = _settings(model_provider_1_base_url="https://x/v1")

    with pytest.raises(ValueError, match="缺少"):
        _ = settings.providers


def test_missing_configuration_only_fails_when_accessed():
    """验证构造配置不校验，只有真正取用 provider 时才报错。"""
    settings = _settings()

    assert settings.model_provider_1_base_url is None
    with pytest.raises(ValueError, match="未配置任何模型 provider"):
        _ = settings.providers


def test_normalize_models_ignores_non_string_entries():
    """验证模型规范器容忍空值与非法类型。"""
    assert normalize_models(None) == ()
    assert normalize_models(("a", 1, "b")) == ("a", "b")
    assert normalize_models(",, ") == ()


class _QuotaExceeded(RuntimeError):
    """模拟额度耗尽的模型服务错误。"""

    status_code = 429


class _FakeClient:
    """记录请求并按需失败的 OpenAI 兼容客户端替身。"""

    def __init__(self, name: str, fail: bool) -> None:
        self.name = name
        self.fail = fail
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        attempts.append(f"{self.name}/{kwargs['model']}")
        if self.fail:
            raise _QuotaExceeded("quota exhausted")
        return f"ok:{kwargs['model']}"


attempts: list[str] = []


def test_adapter_fails_over_across_providers():
    """验证同一 provider 内耗尽后切换到下一个 provider。"""
    attempts.clear()
    clients = {
        "p1": _FakeClient("p1", fail=True),
        "p2": _FakeClient("p2", fail=False),
    }
    providers = (
        ProviderConfig("p1", "https://p1/v1", "k1", ("m1", "m1b")),
        ProviderConfig("p2", "https://p2/v1", "k2", ("m2",)),
    )
    adapter = ModelAdapter(
        providers=providers,
        client_factory=lambda provider: clients[provider.name],
    )

    response = adapter.chat([{"role": "user", "content": "x"}])

    assert response == "ok:m2"
    assert attempts == ["p1/m1", "p1/m1b", "p2/m2"]
    assert adapter.provider == "p2"
    assert adapter.model == "m2"


def test_adapter_reports_all_models_when_every_provider_fails():
    """验证全部候选耗尽时错误信息列出所有模型名。"""
    attempts.clear()
    clients = {"p1": _FakeClient("p1", fail=True), "p2": _FakeClient("p2", fail=True)}
    providers = (
        ProviderConfig("p1", "https://p1/v1", "k1", ("m1",)),
        ProviderConfig("p2", "https://p2/v1", "k2", ("m2",)),
    )
    adapter = ModelAdapter(
        providers=providers,
        client_factory=lambda provider: clients[provider.name],
    )

    with pytest.raises(ModelFallbackExhaustedError, match="m1, m2"):
        adapter.chat([{"role": "user", "content": "x"}])


def test_unsupported_provider_index_is_rejected():
    """验证超过上限的 provider 序号显式报错，而不是被静默忽略。"""
    with pytest.raises(ValueError, match="最多支持 5 个 provider"):
        reject_unsupported_provider_indices({"MODEL_PROVIDER_6_API_KEY": "key"})

    assert MAX_PROVIDERS == 5


def test_supported_indices_and_unrelated_keys_pass():
    """验证上限内的序号与无关变量不会被误判。"""
    reject_unsupported_provider_indices({"MODEL_PROVIDER_5_API_KEY": "key"})
    reject_unsupported_provider_indices({"MODEL_PROVIDER_TIMEOUT": "30"})
    reject_unsupported_provider_indices({"QWEN_API_KEY": "key"})
