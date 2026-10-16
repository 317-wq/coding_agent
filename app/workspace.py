from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceContext:
    """单次 Agent 运行共享的受控工作区上下文。"""

    root: Path

    @classmethod
    def from_path(cls, path: Path) -> "WorkspaceContext":
        expanded = path.expanduser()

        if not expanded.exists():
            raise ValueError(f"工作区不存在：{expanded}")

        if not expanded.is_dir():
            raise ValueError(f"工作区不是目录：{expanded}")

        return cls(root=expanded.resolve())

    def resolve(self, path: str) -> Path:
        candidate = Path(path)

        if candidate.is_absolute():
            raise ValueError("不允许使用绝对路径。")

        target = (self.root / candidate).resolve()

        try:
            target.relative_to(self.root)
        except ValueError:
            raise ValueError("禁止访问工作目录之外的路径。") from None

        return target
