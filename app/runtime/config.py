"""读取和校验 Agent 的运行时配置。

配置采用惰性加载：导入本模块不会读取 .env、也不会校验必填项，只有真正
需要 provider 时才解析。这样在没有 .env 的干净克隆里也能导入模块和收集测试。

支持的配置格式：

- 新格式：``MODEL_PROVIDER_n_NAME / BASE_URL / API_KEY / MODELS``，最多 5 个
  provider，``MODELS`` 用逗号分隔，按 n 的顺序决定故障转移顺序。
- 兼容格式：``QWEN_API_KEY / QWEN_BASE_URL / QWEN_CHAT_MODEL_1..10``，
  会被映射为单个名为 ``qwen`` 的 provider；新格式存在时优先使用新格式。
"""

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.runtime.provider import ProviderConfig, normalize_models

MAX_PROVIDERS = 5
"""支持显式配置的 provider 数量上限，超出的序号会被忽略。"""

LEGACY_MODEL_SLOTS = 10
"""旧格式 QWEN_CHAT_MODEL_n 的槽位数量。"""


def _clean(value: object) -> str | None:
    """把可选字符串配置规范化为去空白后的值。"""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


class Settings(BaseSettings):
    """读取并验证 Mini Coding Agent 的环境配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    model_provider_1_name: str | None = None
    model_provider_1_base_url: str | None = None
    model_provider_1_api_key: str | None = None
    model_provider_1_models: str | None = None
    model_provider_2_name: str | None = None
    model_provider_2_base_url: str | None = None
    model_provider_2_api_key: str | None = None
    model_provider_2_models: str | None = None
    model_provider_3_name: str | None = None
    model_provider_3_base_url: str | None = None
    model_provider_3_api_key: str | None = None
    model_provider_3_models: str | None = None
    model_provider_4_name: str | None = None
    model_provider_4_base_url: str | None = None
    model_provider_4_api_key: str | None = None
    model_provider_4_models: str | None = None
    model_provider_5_name: str | None = None
    model_provider_5_base_url: str | None = None
    model_provider_5_api_key: str | None = None
    model_provider_5_models: str | None = None

    qwen_api_key: str | None = None
    qwen_base_url: str | None = None
    qwen_chat_model_1: str | None = None
    qwen_chat_model_2: str | None = None
    qwen_chat_model_3: str | None = None
    qwen_chat_model_4: str | None = None
    qwen_chat_model_5: str | None = None
    qwen_chat_model_6: str | None = None
    qwen_chat_model_7: str | None = None
    qwen_chat_model_8: str | None = None
    qwen_chat_model_9: str | None = None
    qwen_chat_model_10: str | None = None

    @property
    def providers(self) -> tuple[ProviderConfig, ...]:
        """按配置顺序返回 provider 序列；新格式优先于旧格式。"""
        explicit = tuple(
            provider
            for provider in (
                self._explicit_provider(index)
                for index in range(1, MAX_PROVIDERS + 1)
            )
            if provider is not None
        )
        if explicit:
            return explicit
        legacy = self._legacy_provider()
        if legacy is not None:
            return (legacy,)
        raise ValueError(
            "未配置任何模型 provider：请设置 MODEL_PROVIDER_1_BASE_URL、"
            "MODEL_PROVIDER_1_API_KEY、MODEL_PROVIDER_1_MODELS，"
            "或沿用旧的 QWEN_API_KEY、QWEN_BASE_URL、QWEN_CHAT_MODEL_1。"
        )

    @property
    def qwen_chat_models(self) -> tuple[str, ...]:
        """兼容旧调用方：返回旧格式 QWEN_CHAT_MODEL_n 的模型序列。"""
        return normalize_models(
            tuple(
                getattr(self, f"qwen_chat_model_{index}")
                for index in range(1, LEGACY_MODEL_SLOTS + 1)
            )
        )

    def _explicit_provider(self, index: int) -> ProviderConfig | None:
        """解析第 index 个显式 provider；未配置时返回 None。"""
        base_url = _clean(getattr(self, f"model_provider_{index}_base_url"))
        api_key = _clean(getattr(self, f"model_provider_{index}_api_key"))
        models = normalize_models(getattr(self, f"model_provider_{index}_models"))
        if not any((base_url, api_key, models)):
            return None
        missing = [
            label
            for label, value in (
                ("MODEL_PROVIDER_" + str(index) + "_BASE_URL", base_url),
                ("MODEL_PROVIDER_" + str(index) + "_API_KEY", api_key),
                ("MODEL_PROVIDER_" + str(index) + "_MODELS", models),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"MODEL_PROVIDER_{index} 配置不完整，缺少：{'、'.join(missing)}。"
            )
        name = _clean(getattr(self, f"model_provider_{index}_name")) or f"provider-{index}"
        return ProviderConfig(
            name=name,
            base_url=base_url,
            api_key=api_key,
            models=models,
        )

    def _legacy_provider(self) -> ProviderConfig | None:
        """把旧的 QWEN_* 配置映射成单个 provider。"""
        api_key = _clean(self.qwen_api_key)
        base_url = _clean(self.qwen_base_url)
        models = normalize_models(
            tuple(
                getattr(self, f"qwen_chat_model_{index}")
                for index in range(1, LEGACY_MODEL_SLOTS + 1)
            )
        )
        if not any((api_key, base_url, models)):
            return None
        missing = [
            label
            for label, value in (
                ("QWEN_API_KEY", api_key),
                ("QWEN_BASE_URL", base_url),
                ("QWEN_CHAT_MODEL_1", models),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"旧版 Qwen 配置不完整，缺少：{'、'.join(missing)}。"
            )
        return ProviderConfig(
            name="qwen",
            base_url=base_url,
            api_key=api_key,
            models=models,
        )


def reject_unsupported_provider_indices(
    environ: Mapping[str, str] | None = None,
) -> None:
    """拒绝超出上限的 MODEL_PROVIDER_n_* 变量，避免配置被静默忽略。

    只能检查真实环境变量；写在 .env 文件里的超限序号不会被发现，因为
    pydantic 的 extra="ignore" 会直接丢弃未声明的字段。
    """
    source = os.environ if environ is None else environ
    for key in source:
        upper = key.upper()
        if not upper.startswith("MODEL_PROVIDER_"):
            continue
        index_text, separator, _ = upper[len("MODEL_PROVIDER_") :].partition("_")
        if not separator or not index_text.isdigit():
            continue
        if int(index_text) > MAX_PROVIDERS:
            raise ValueError(
                f"不支持的 provider 序号：{key}；最多支持 {MAX_PROVIDERS} 个 provider。"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """惰性加载并缓存配置；首次调用时检查环境变量再读取配置。"""
    reject_unsupported_provider_indices()
    return Settings()


def reload_settings() -> Settings:
    """清空缓存并重新读取配置，供测试和运行时切换使用。"""
    get_settings.cache_clear()
    return get_settings()


class _LazySettings:
    """兼容旧 `from app.runtime.config import settings` 的惰性代理。

    只在属性被访问时才加载配置，因此不会重新引入“导入即校验”。
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_settings(), name)


settings = _LazySettings()
