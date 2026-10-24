"""测试环境的默认模型配置。

保证没有 `.env` 的干净克隆也能导入应用并收集测试：所有用例都不依赖开发者
本机的真实模型凭证。真实环境变量优先，因此不会覆盖显式配置。
"""

import os

DEFAULT_PROVIDER_ENV: dict[str, str] = {
    "MODEL_PROVIDER_1_NAME": "test",
    "MODEL_PROVIDER_1_BASE_URL": "https://example.invalid/v1",
    "MODEL_PROVIDER_1_API_KEY": "test-key",
    "MODEL_PROVIDER_1_MODELS": "test-model",
}

for _key, _value in DEFAULT_PROVIDER_ENV.items():
    os.environ.setdefault(_key, _value)
