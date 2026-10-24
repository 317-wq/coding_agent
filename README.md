# Mini Coding Agent

一个面向本地工作区的命令行 Coding Agent：接收自然语言编程任务，读取项目结构，调用受控工具修改文件，运行测试，并输出可审查的任务报告。

## 特性

- 支持通过 `--workspace` 指定受控工作区，所有文件操作限制在该目录内。
- 使用 Typer 构建类型安全的命令行参数，使用 Rich 渲染帮助页、执行进度和任务报告。
- 默认隐藏模型轮次、工具调用和 Trace 细节，需要排查问题时可通过 `--debug` 完整显示。
- 使用结构化计划跟踪“阅读 → 实现 → 编写测试 → 运行测试”阶段。
- 内置文件读写、目录管理、代码搜索、pytest、只读 Git 查询等工具。
- 写入前创建快照，写入后核验磁盘内容；重复写入和越界访问会被拦截。
- 测试失败后进入“分析 → 修改 → 重跑”恢复流程，并限制自动修复次数。
- 输出中文任务报告，也可以通过 `--output-format json` 输出机器可读结果。
- 支持模型候选故障转移：按 `QWEN_CHAT_MODEL_1`、`QWEN_CHAT_MODEL_2`……顺序选择可用模型。

## 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- 一个 OpenAI 兼容 API（默认示例使用阿里云百炼）

安装依赖：

```bash
uv sync
```

## 配置模型

复制环境变量模板并填写至少一组 API Key 和模型：

```bash
cp .env.example .env
```

`.env` 不应提交到 Git。常用配置如下：

```dotenv
# 新格式：每个 provider 一组配置，MODELS 用英文逗号分隔
MODEL_PROVIDER_1_NAME=qwen
MODEL_PROVIDER_1_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_PROVIDER_1_API_KEY=your-api-key
MODEL_PROVIDER_1_MODELS=qwen3.7-flash,qwen3.8-max
```

同一个 provider 内按 `MODELS` 顺序故障转移，多个 provider 之间按序号顺序切换（最多 5 个）。只有额度、限流或模型不可用时才切换，其他错误直接报错。

旧配置 `QWEN_API_KEY` / `QWEN_BASE_URL` / `QWEN_CHAT_MODEL_1..10` 仍然可用，会被映射为单个 `qwen` provider；两种格式同时存在时优先使用新格式。

## 运行

推荐直接使用项目命令启动交互式 Agent：

```bash
uv run coding-agent --workspace ./workspace/demo
```

仍可使用模块形式：`uv run python -m app.cli.main --workspace ./workspace/demo`。

在交互终端中，按 Enter 提交任务，按 Shift+Enter 插入换行；如果终端无法区分 Shift+Enter，可使用 Alt+Enter。一次粘贴的多行文本会完整保留在同一个输入缓冲区中，粘贴完成后按 Enter 即可提交。输入 `exit` 或 `quit` 退出。

常用参数：

```bash
# 输出 JSON 报告
uv run coding-agent \
  --workspace ./workspace/demo \
  --output-format json

# 增大复杂任务的执行资源上限
uv run coding-agent \
  --workspace ./workspace/demo \
  --max-steps 50 \
  --max-tool-calls 120

# 显示完整 Trace、模型轮次和工具调用日志
uv run coding-agent \
  --workspace ./workspace/demo \
  --debug
```

### 更简单的一次性调用

非交互式管道输入继续使用单独一行 `END` 作为任务结束标记。例如 Shell here-document：

```bash
uv run coding-agent --workspace ./workspace/demo <<'TASK'
实现用户登录功能，并补充 pytest 测试，运行测试确认通过。
END
TASK
```

也可以保存成脚本，之后重复执行：

```bash
#!/usr/bin/env bash
set -euo pipefail

uv run coding-agent \
  --workspace "${1:-./workspace/demo}" \
  --max-steps 50 \
  --max-tool-calls 120 <<'TASK'
请检查当前项目，实现本次需求，补充测试并运行测试。
END
TASK
```

## 测试与代码检查

运行全部测试：

```bash
uv run pytest -q
```

运行 Ruff：

```bash
uv run ruff check app tests
```

## 项目结构

```text
app/
├── cli/                 # 命令行入口和报告渲染
├── core/                # Agent Loop、Planner、上下文、Trace、完成判定
├── runtime/             # 工作区、模型和项目探索
└── tools/               # 工具注册、校验、安全策略和内置工具
MVP/
├── PROJECT.md           # 当前架构与接口基线
└── MVP-*.md             # 各版本增量说明
diagram/                 # Mermaid 架构和流程图
tests/                   # pytest 测试
```

## 安全边界

- 文件和测试工具只能访问指定工作区。
- 删除、覆盖等高风险操作需要授权；Agent 不提供 Git 提交、重置或清理操作。
- 测试失败或环境错误会保留证据，不会静默报告为成功。
- 建议在独立分支或临时工作区运行自动修改任务。

## 当前验证

当前主项目测试与 Ruff 检查均已通过；最近一次生成的示例项目独立运行结果为 `32 passed`。详细版本变更和验收标准见 [`MVP/PROJECT.md`](MVP/PROJECT.md) 与 [`MVP/MVP-0.5.md`](MVP/MVP-0.5.md)。

## License

尚未指定许可证。如需公开发布到 GitHub，建议补充 `LICENSE` 文件并明确授权条款。
