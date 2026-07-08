from __future__ import annotations

import os
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from aigamedevbench.git_ops import git_run


def _rmtree(path: Path) -> None:
    """Remove a tree, clearing read-only bits (Windows keeps .git packs read-only)."""
    def on_error(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=on_error)


def _new_work_dir(workspace_root: Path | str | None, workspace_name: str | None = None) -> Path:
    # An AI harness running inside the workspace may flag edits under the OS temp
    # dir as needing manual approval (its permission gate distrusts temp paths),
    # which silently hangs the run. --workspace-root lets the caller place
    # workspaces under a trusted directory instead.
    base = Path(workspace_root) if workspace_root is not None else Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    if workspace_name is not None:
        work_dir = base / workspace_name
        if work_dir.exists():
            _rmtree(work_dir)
        return work_dir
    return base / f"aigdbench_ws_{uuid.uuid4().hex}"


@contextmanager
def isolated_workspace(repo_root: Path, baseline_ref: str,
                       workspace_root: Path | str | None = None,
                       workspace_name: str | None = None,
                       keep_workspace: bool = False) -> Iterator[Path]:
    work_dir = _new_work_dir(workspace_root, workspace_name)
    git_run(["worktree", "add", "--detach", str(work_dir), baseline_ref], cwd=repo_root)
    try:
        yield work_dir
    finally:
        if keep_workspace:
            return
        try:
            git_run(["worktree", "remove", "--force", str(work_dir)], cwd=repo_root)
        except Exception:
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)


@contextmanager
def folder_workspace(baseline_dir: Path,
                     workspace_root: Path | str | None = None,
                     workspace_name: str | None = None,
                     keep_workspace: bool = False) -> Iterator[Path]:
    """Workspace for folder-type testcases: copy a self-contained project dir,
    init a throwaway git repo so the driver (git apply) and change detection
    (git status) work the same as the git-worktree path."""
    if not baseline_dir.is_dir():
        raise FileNotFoundError(f"baseline dir not found: {baseline_dir}")
    work_dir = _new_work_dir(workspace_root, workspace_name)
    shutil.copytree(baseline_dir, work_dir)
    git_run(["init", "-q"], cwd=work_dir)
    git_run(["add", "-A"], cwd=work_dir)
    git_run(
        ["-c", "user.email=bench@aigdbench.local", "-c", "user.name=aigdbench",
         "commit", "-q", "-m", "baseline"],
        cwd=work_dir,
    )
    try:
        yield work_dir
    finally:
        if keep_workspace:
            return
        if work_dir.exists():
            _rmtree(work_dir)


def apply_patch(workspace: Path, patch_text: str) -> None:
    # --ignore-whitespace tolerates CRLF/LF and trailing-whitespace differences
    # between the patch and the working tree. On Windows a patch generated with
    # LF (or CRLF) routinely mismatches the checked-out line endings, and a
    # harness-produced diff may differ in whitespace too; the benchmark scores
    # the semantic change, so whitespace-only mismatches must not block apply.
    git_run(["apply", "--whitespace=nowarn", "--ignore-whitespace", "-"],
            cwd=workspace, input=patch_text)
