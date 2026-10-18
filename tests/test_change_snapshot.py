import json
import os
from pathlib import Path

import pytest

from app.runtime.workspace import WorkspaceContext
from app.tools.change_snapshot import SnapshotError, TaskSnapshotStore


def test_captures_existing_file_content_permission_and_metadata(tmp_path: Path):
    target = tmp_path / "script.sh"
    target.write_bytes(b"#!/bin/sh\necho before\n")
    target.chmod(0o750)
    store = TaskSnapshotStore(WorkspaceContext.from_path(tmp_path))

    snapshot = store.capture("script.sh")

    assert snapshot.existed is True
    assert snapshot.content == b"#!/bin/sh\necho before\n"
    assert snapshot.mode == 0o750
    assert snapshot.to_dict()["mode"] == "0750"
    assert "content" not in snapshot.to_dict()
    assert json.loads(store.to_json()) == store.to_dict()


def test_repeated_capture_keeps_first_state(tmp_path: Path):
    target = tmp_path / "module.py"
    target.write_text("first", encoding="utf-8")
    store = TaskSnapshotStore(WorkspaceContext.from_path(tmp_path))

    first = store.capture("module.py")
    target.write_text("second", encoding="utf-8")
    second = store.capture("./module.py")

    assert second is first
    assert second.content == b"first"
    assert len(store.snapshots) == 1


def test_captures_originally_missing_path(tmp_path: Path):
    store = TaskSnapshotStore(WorkspaceContext.from_path(tmp_path))

    snapshot = store.capture("new.py")
    (tmp_path / "new.py").write_text("created", encoding="utf-8")

    assert snapshot.existed is False
    assert snapshot.content is None
    assert snapshot.mode is None


def test_binary_content_is_preserved_as_bytes(tmp_path: Path):
    (tmp_path / "data.bin").write_bytes(b"\x00\xff\x01")
    store = TaskSnapshotStore(WorkspaceContext.from_path(tmp_path))

    assert store.capture("data.bin").content == b"\x00\xff\x01"


def test_rejects_oversized_directory_outside_and_symlink_paths(tmp_path: Path):
    (tmp_path / "large.txt").write_bytes(b"12345")
    (tmp_path / "folder").mkdir()
    store = TaskSnapshotStore(
        WorkspaceContext.from_path(tmp_path), max_file_size=4
    )

    with pytest.raises(SnapshotError, match="单文件快照上限"):
        store.capture("large.txt")
    with pytest.raises(SnapshotError, match="不是普通文件"):
        store.capture("folder")
    with pytest.raises(SnapshotError, match="工作目录之外"):
        store.capture("../outside.txt")

    if hasattr(os, "symlink"):
        (tmp_path / "target.txt").write_text("target", encoding="utf-8")
        (tmp_path / "link.txt").symlink_to("target.txt")
        with pytest.raises(SnapshotError, match="符号链接"):
            store.capture("link.txt")


def test_enforces_file_count_and_total_size_limits(tmp_path: Path):
    (tmp_path / "one.txt").write_bytes(b"123")
    (tmp_path / "two.txt").write_bytes(b"456")
    store = TaskSnapshotStore(
        WorkspaceContext.from_path(tmp_path), max_files=2, max_total_size=5
    )

    store.capture("one.txt")
    with pytest.raises(SnapshotError, match="总大小"):
        store.capture("two.txt")

    count_limited = TaskSnapshotStore(
        WorkspaceContext.from_path(tmp_path), max_files=1
    )
    count_limited.capture("one.txt")
    with pytest.raises(SnapshotError, match="数量"):
        count_limited.capture("missing.txt")
