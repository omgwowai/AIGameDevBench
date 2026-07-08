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


def _normalize_hunk_blank_lines(patch_text: str) -> str:
    """Give blank *context* lines inside a hunk their required leading space.

    A unified-diff context line for a blank source line must be a single space
    (" "), not an empty string. Many diff producers (editors, copy-paste, and
    LLM-authored patches) emit a truly empty line instead, which `git apply`
    rejects ("corrupt patch" / hunk mismatch) even though the change is correct.
    This walks each hunk body and rewrites a wholly-empty line to " " so the
    patch applies. Only lines *inside* a hunk (after an "@@ ... @@" header and
    before the next diff/hunk header) are touched; file headers and metadata are
    left exactly as-is.

    Conservative: a line that already begins with " ", "+", "-", or "\\" is a
    well-formed hunk line and is never altered — we only fix the empty-line case.
    """
    out: list[str] = []
    in_hunk = False
    parts = patch_text.split("\n")
    # A trailing "\n" makes split() yield a final "" that is not a real line but
    # the terminator artifact — never rewrite it.
    last_idx = len(parts) - 1
    for i, line in enumerate(parts):
        if line.startswith("@@"):
            in_hunk = True
            out.append(line)
            continue
        # Any of these header/boundary markers ends the current hunk body.
        if line.startswith(("diff --git", "--- ", "+++ ", "index ",
                            "old mode", "new mode", "similarity ", "rename ",
                            "copy ", "deleted file", "new file", "Binary ")):
            in_hunk = False
            out.append(line)
            continue
        if in_hunk and line == "" and i != last_idx:
            # A blank line inside a hunk body is a blank context line: prefix the
            # required single space so git accepts it. (Skip the trailing "".)
            out.append(" ")
            continue
        out.append(line)
    normalized = "\n".join(out)
    # Preserve a single trailing newline (patch_text usually ends with one).
    if patch_text.endswith("\n") and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def apply_patch(workspace: Path, patch_text: str) -> None:
    # --ignore-whitespace tolerates CRLF/LF and trailing-whitespace differences
    # between the patch and the working tree. On Windows a patch generated with
    # LF (or CRLF) routinely mismatches the checked-out line endings, and a
    # harness-produced diff may differ in whitespace too; the benchmark scores
    # the semantic change, so whitespace-only mismatches must not block apply.
    # Normalize blank context lines first (empty -> " ") so a diff whose blank
    # lines lost their leading space still applies.
    patch_text = _normalize_hunk_blank_lines(patch_text)
    # --recount lets git recompute each hunk's line counts from the body instead
    # of trusting the "@@ -a,b +c,d @@" header. Harness/LLM-authored diffs
    # routinely miscount those (off-by-one when adding/removing lines), which git
    # otherwise rejects as "corrupt patch"; the actual +/-/context lines are the
    # source of truth, so recounting makes a semantically-correct diff apply.
    git_run(["apply", "--whitespace=nowarn", "--ignore-whitespace", "--recount", "-"],
            cwd=workspace, input=patch_text)
