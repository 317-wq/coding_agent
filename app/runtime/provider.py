"""模型 provider 配置：把连接信息与候选模型序列绑定为一个不可变对象。

一个 provider 对应一个 OpenAI 兼容服务。模型故障转移先在同一 provider 的
models 序列内进行，再按 provider 顺序切换到下一个服务。
"""

from dataclasses import dataclass

MAX_PROVIDER_MODELS = 10
"""单个 provider 最多登记的候选模型数量，避免配置失控。"""


def normalize_models(models: object) -> tuple[str, ...]:
    """把逗号分隔的模型配置规范化为去重、去空的模型序列。"""
    if models is None:
        return ()
    if isinstance(models, str):
        candidates = models.split(",")
    elif isinstance(models, (list, tuple, set)):
        candidates = list(models)
    else:
        return ()
    cleaned = (
        candidate.strip()
        for candidate in candidates
        if isinstance(candidate, str) and candidate.strip()
    )
    return tuple(dict.fromkeys(cleaned))


@dataclass(frozen=True)
class ProviderConfig:
    """一个 OpenAI 兼容模型服务的连接配置与候选模型序列。"""

    name: str
    base_url: str
    api_key: str
    models: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("provider 名称不能为空。")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError(f"provider {self.name} 缺少 base_url。")
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError(f"provider {self.name} 缺少 api_key。")
        if not self.models:
            raise ValueError(f"provider {self.name} 至少需要一个模型名。")
        if any(not model.strip() for model in self.models):
            raise ValueError(f"provider {self.name} 的模型名不能为空。")
        if len(self.models) > MAX_PROVIDER_MODELS:
            raise ValueError(
                f"provider {self.name} 的模型数量超过上限 {MAX_PROVIDER_MODELS}。"
            )

    def to_dict(self) -> dict[str, object]:
        """返回不含 api_key 的可序列化描述，避免配置泄漏。"""
        return {
            "name": self.name,
            "base_url": self.base_url,
            "models": list(self.models),
        }
