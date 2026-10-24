"""封装模型客户端，并在模型额度耗尽或不可用时顺序故障转移。

候选顺序为：先按 provider 配置顺序，再在每个 provider 的 models 序列内依次
尝试。只有额度、限流、模型缺失或不可用错误才切换，其他错误直接抛出，避免
掩盖真实问题。最近一次成功的候选会被持久复用。
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from app.runtime.config import get_settings
from app.runtime.provider import ProviderConfig

LOGGER = logging.getLogger(__name__)

_FALLBACK_STATUS_CODES = {404, 429}
_FALLBACK_MARKERS = (
    "insufficient_quota",
    "quota exceeded",
    "quota exhausted",
    "allocationquota",
    "free tier",
    "insufficient balance",
    "model_not_found",
    "model not found",
    "model unavailable",
    "model is not available",
    "no available model",
    "额度",
    "配额",
    "模型不存在",
    "模型不可用",
)

ClientFactory = Callable[[ProviderConfig], Any]
"""按 provider 创建 OpenAI 兼容客户端的工厂，供测试注入。"""


class ModelFallbackExhaustedError(RuntimeError):
    """所有配置模型均因额度或可用性错误失败。"""


@dataclass(frozen=True)
class ModelEndpoint:
    """一个可尝试的（provider, 模型）组合及其客户端。"""

    provider: str
    model: str
    client: Any


class ModelAdapter:
    """封装 API 调用，并持久使用最近一次成功的候选模型。"""

    def __init__(
        self,
        client: Any | None = None,
        models: Sequence[str] | None = None,
        providers: Sequence[ProviderConfig] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        """初始化候选序列：显式 models 优先，其次 providers，最后读取配置。"""
        self._client_factory = client_factory
        self._shared_client = client
        self._endpoints = self._build_endpoints(models, providers)
        self._index = 0

    @property
    def provider(self) -> str:
        """返回当前正在使用的 provider 名称。"""
        return self._endpoints[self._index].provider

    @property
    def model(self) -> str:
        """返回当前正在使用的模型名称。"""
        return self._endpoints[self._index].model

    @property
    def models(self) -> tuple[str, ...]:
        """按尝试顺序返回全部候选模型名。"""
        return tuple(endpoint.model for endpoint in self._endpoints)

    def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict] | None = None,
    ) -> ChatCompletion:
        """发送请求；仅对额度、限流或模型不可用错误切换候选。"""
        last_error: Exception | None = None
        for index in range(self._index, len(self._endpoints)):
            endpoint = self._endpoints[index]
            request_params: dict[str, Any] = {
                "model": endpoint.model,
                "messages": messages,
            }
            if tools:
                request_params["tools"] = tools
                request_params["tool_choice"] = "auto"
            try:
                response = endpoint.client.chat.completions.create(**request_params)
            except Exception as exc:
                if not self._is_fallback_error(exc):
                    raise
                last_error = exc
                if index + 1 < len(self._endpoints):
                    following = self._endpoints[index + 1]
                    LOGGER.warning(
                        "模型 %s/%s 不可用，自动切换到 %s/%s。",
                        endpoint.provider,
                        endpoint.model,
                        following.provider,
                        following.model,
                    )
                    self._index = index + 1
                    continue
                break
            self._index = index
            return response
        names = ", ".join(self.models)
        raise ModelFallbackExhaustedError(
            f"所有已配置模型均不可用：{names}"
        ) from last_error

    def _build_endpoints(
        self,
        models: Sequence[str] | None,
        providers: Sequence[ProviderConfig] | None,
    ) -> tuple[ModelEndpoint, ...]:
        """按显式 models、显式 providers、运行时配置的优先级构建候选序列。"""
        if models is not None:
            configured = tuple(
                dict.fromkeys(
                    model.strip()
                    for model in models
                    if isinstance(model, str) and model.strip()
                )
            )
            if not configured:
                raise ValueError("至少需要配置一个模型名称。")
            provider = self._implicit_provider()
            provider_name = provider.name if provider is not None else "default"
            client = self._client_for(provider)
            return tuple(
                ModelEndpoint(provider_name, model, client) for model in configured
            )

        chosen = tuple(providers) if providers is not None else get_settings().providers
        if not chosen:
            raise ValueError("至少需要配置一个模型 provider。")
        endpoints: list[ModelEndpoint] = []
        for provider in chosen:
            client = self._client_for(provider)
            endpoints.extend(
                ModelEndpoint(provider.name, model, client)
                for model in provider.models
            )
        return tuple(endpoints)

    def _implicit_provider(self) -> ProviderConfig | None:
        """显式传入 models 时，只在必须创建客户端时读取配置。"""
        if self._shared_client is not None and self._client_factory is None:
            return None
        return get_settings().providers[0]

    def _client_for(self, provider: ProviderConfig | None) -> Any:
        """按 provider 创建客户端；测试可注入共享客户端或工厂。"""
        if self._shared_client is not None:
            return self._shared_client
        if self._client_factory is not None:
            if provider is None:
                raise ValueError("缺少 provider 配置，无法创建模型客户端。")
            return self._client_factory(provider)
        if provider is None:
            raise ValueError("缺少 provider 配置，无法创建模型客户端。")
        return OpenAI(api_key=provider.api_key, base_url=provider.base_url)

    @staticmethod
    def _is_fallback_error(exc: Exception) -> bool:
        """识别应切换候选的额度、限流、缺失和不可用错误。"""
        status_code = getattr(exc, "status_code", None)
        if status_code in _FALLBACK_STATUS_CODES:
            return True
        body = getattr(exc, "body", None)
        details = f"{exc} {body}".lower()
        return any(marker in details for marker in _FALLBACK_MARKERS)
