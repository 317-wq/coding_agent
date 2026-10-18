from openai import OpenAI
from openai.types.chat import ChatCompletion
from openai.types.chat import ChatCompletionMessageParam

from app.config import settings


class ModelAdapter:
    """封装大模型 API 调用。"""

    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
        )
        self.model = settings.qwen_chat_model_1

    def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict] | None = None,
    ) -> ChatCompletion:
        """发送对话请求，可选提供工具定义。"""

        request_params = {
            "model": self.model,
            "messages": messages,
        }

        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**request_params)
        return response