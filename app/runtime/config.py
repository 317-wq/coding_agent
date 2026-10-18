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


settings = Settings()
