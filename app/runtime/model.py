"""封装模型客户端，并在模型额度耗尽或不可用时顺序故障转移。"""

import logging
from collections.abc import Sequence
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from app.runtime.config import settings

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


class ModelFallbackExhaustedError(RuntimeError):
    """所有配置模型均因额度或可用性错误失败。"""


class ModelAdapter:
    """封装 API 调用，并持久使用最近一次成功的候选模型。"""

    def __init__(
        self,
        client: Any | None = None,
        models: Sequence[str] | None = None,
    ) -> None:
        """初始化客户端及按顺序尝试的非空、去重模型列表。"""
        self.client = client or OpenAI(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
        )
        configured = models if models is not None else settings.qwen_chat_models
        self.models = tuple(dict.fromkeys(model.strip() for model in configured if model.strip()))
        if not self.models:
            raise ValueError("至少需要配置一个 QWEN_CHAT_MODEL_n。")
        self._model_index = 0

    @property
    def model(self) -> str:
        """返回当前正在使用的模型名称。"""
        return self.models[self._model_index]

    def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict] | None = None,
    ) -> ChatCompletion:
        """发送请求；仅对额度、限流或模型不可用错误切换候选模型。"""
        last_error: Exception | None = None
        for index in range(self._model_index, len(self.models)):
            model = self.models[index]
            request_params: dict[str, Any] = {
                "model": model,
                "messages": messages,
            }
            if tools:
                request_params["tools"] = tools
                request_params["tool_choice"] = "auto"
            try:
                response = self.client.chat.completions.create(**request_params)
            except Exception as exc:
                if not self._is_fallback_error(exc):
                    raise
                last_error = exc
                if index + 1 < len(self.models):
                    LOGGER.warning(
                        "模型 %s 不可用，自动切换到 %s。",
                        model,
                        self.models[index + 1],
                    )
                    self._model_index = index + 1
                    continue
                break
            self._model_index = index
            return response
        names = ", ".join(self.models)
        raise ModelFallbackExhaustedError(
            f"所有已配置模型均不可用：{names}"
        ) from last_error

    @staticmethod
    def _is_fallback_error(exc: Exception) -> bool:
        """识别应切换模型的额度、限流、缺失和不可用错误。"""
        status_code = getattr(exc, "status_code", None)
        if status_code in _FALLBACK_STATUS_CODES:
            return True
        body = getattr(exc, "body", None)
        details = f"{exc} {body}".lower()
        return any(marker in details for marker in _FALLBACK_MARKERS)
