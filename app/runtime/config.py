"""读取和校验 Agent 的运行时配置。"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """读取并验证 Mini Coding Agent 的环境配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    qwen_api_key: str = Field(min_length=1)
    qwen_base_url: str = Field(min_length=1)
    qwen_chat_model_1: str = Field(min_length=1)
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
    def qwen_chat_models(self) -> tuple[str, ...]:
        """按数字后缀返回已配置且去重的 Qwen 模型故障转移序列。"""
        configured = (
            self.qwen_chat_model_1,
            self.qwen_chat_model_2,
            self.qwen_chat_model_3,
            self.qwen_chat_model_4,
            self.qwen_chat_model_5,
            self.qwen_chat_model_6,
            self.qwen_chat_model_7,
            self.qwen_chat_model_8,
            self.qwen_chat_model_9,
            self.qwen_chat_model_10,
        )
        return tuple(
            dict.fromkeys(
                name for model in configured if model and (name := model.strip())
            )
        )


settings = Settings()
