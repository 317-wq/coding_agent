"""工作区路径管理与文件访问边界控制。"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceContext:
    """单次 Agent 运行共享的受控工作区上下文。"""

    root: Path

    @classmethod
    def from_path(cls, path: Path) -> "WorkspaceContext":
        """校验并创建指向真实目录的工作区上下文。"""
        expanded = path.expanduser()

        if not expanded.exists():
            raise ValueError(f"工作区不存在：{expanded}")

        if not expanded.is_dir():
            raise ValueError(f"工作区不是目录：{expanded}")

        return cls(root=expanded.resolve())

    def resolve(self, path: str) -> Path:
        """统一解析相对路径或工作区内绝对路径，并拒绝越界。"""
        if not isinstance(path, str) or not path.strip():
            raise ValueError("路径不能为空。")
        candidate = Path(path.strip()).expanduser()
        target = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()

        try:
            target.relative_to(self.root)
        except ValueError:
            raise ValueError("禁止访问工作目录之外的路径。") from None

        return target

    def relative_path(self, path: str) -> str:
        """返回规范的工作区相对 POSIX 路径，根目录表示为点号。"""
        target = self.resolve(path)
        relative = target.relative_to(self.root).as_posix()
        return relative or "."
