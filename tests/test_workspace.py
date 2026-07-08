from __future__ import annotations

from pathlib import Path

import pytest

from aigamedevbench.workspace import (
    isolated_workspace, folder_workspace, apply_patch, _normalize_hunk_blank_lines,
)
from aigamedevbench.git_ops import git_run


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    git_run(["init"], cwd=path)
    git_run(["config", "user.email", "t@t.com"], cwd=path)
    git_run(["config", "user.name", "t"], cwd=path)
    (path / "file.txt").write_text("hello\n", encoding="utf-8")
    git_run(["add", "file.txt"], cwd=path)
    git_run(["commit", "-m", "init"], cwd=path)
    return git_run(["rev-parse", "HEAD"], cwd=path).strip()


def test_worktree_created_and_removed(tmp_path):
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    captured = {}
    with isolated_workspace(repo, head) as ws:
        assert (ws / "file.txt").read_text(encoding="utf-8") == "hello\n"
        captured["ws"] = ws
        assert ws.exists()
    assert not captured["ws"].exists()


def test_changes_in_worktree_isolated(tmp_path):
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    with isolated_workspace(repo, head) as ws:
        (ws / "file.txt").write_text("changed\n", encoding="utf-8")
    assert (repo / "file.txt").read_text(encoding="utf-8") == "hello\n"


def _make_baseline(path: Path) -> None:
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "scripts" / "main.gd").write_text("extends Node\n", encoding="utf-8")


def test_folder_workspace_copies_and_inits_git(tmp_path):
    baseline = tmp_path / "baseline"
    _make_baseline(baseline)
    captured = {}
    with folder_workspace(baseline) as ws:
        assert (ws / "scripts" / "main.gd").read_text(encoding="utf-8") == "extends Node\n"
        # fresh git repo with a clean tree (no changes yet)
        status = git_run(["status", "--porcelain"], cwd=ws)
        assert status.strip() == ""
        captured["ws"] = ws
    assert not captured["ws"].exists()


def test_folder_workspace_changes_are_visible(tmp_path):
    baseline = tmp_path / "baseline"
    _make_baseline(baseline)
    with folder_workspace(baseline) as ws:
        (ws / "scripts" / "main.gd").write_text("extends Node\n# edit\n", encoding="utf-8")
        status = git_run(["status", "--porcelain"], cwd=ws)
        assert "scripts/main.gd" in status
    # baseline itself is untouched
    assert (baseline / "scripts" / "main.gd").read_text(encoding="utf-8") == "extends Node\n"


def test_folder_workspace_apply_patch(tmp_path):
    baseline = tmp_path / "baseline"
    _make_baseline(baseline)
    patch = (
        "diff --git a/scripts/main.gd b/scripts/main.gd\n"
        "--- a/scripts/main.gd\n"
        "+++ b/scripts/main.gd\n"
        "@@ -1 +1,2 @@\n"
        " extends Node\n"
        "+# patched\n"
    )
    with folder_workspace(baseline) as ws:
        apply_patch(ws, patch)
        assert "# patched" in (ws / "scripts" / "main.gd").read_text(encoding="utf-8")


def test_apply_patch_tolerates_crlf_patch_on_lf_tree(tmp_path):
    # Regression: baseline files are LF but a patch generated on Windows can be
    # CRLF. Without --ignore-whitespace, git apply rejects it ("patch does not
    # apply"). The benchmark scores the semantic change, so this must succeed.
    baseline = tmp_path / "baseline"
    _make_baseline(baseline)  # writes scripts/main.gd with LF
    patch_lf = (
        "diff --git a/scripts/main.gd b/scripts/main.gd\n"
        "--- a/scripts/main.gd\n"
        "+++ b/scripts/main.gd\n"
        "@@ -1 +1,2 @@\n"
        " extends Node\n"
        "+# patched\n"
    )
    patch_crlf = patch_lf.replace("\n", "\r\n")
    with folder_workspace(baseline) as ws:
        apply_patch(ws, patch_crlf)
        assert "# patched" in (ws / "scripts" / "main.gd").read_text(encoding="utf-8")


def test_folder_workspace_missing_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        with folder_workspace(tmp_path / "nope"):
            pass


# --- blank-context-line normalization (empty hunk lines -> " ") ---

def test_normalize_hunk_blank_lines_fixes_empty_context():
    # A blank context line emitted as "" (no leading space) is invalid; it must
    # become " ". +/-/space lines and all headers are left untouched.
    patch = (
        "diff --git a/f.gd b/f.gd\n"
        "--- a/f.gd\n"
        "+++ b/f.gd\n"
        "@@ -1,3 +1,3 @@\n"
        " extends Node\n"
        "\n"                        # <-- empty context line (the bug)
        "-var x = 1\n"
        "+var x = 2\n"
    )
    out = _normalize_hunk_blank_lines(patch)
    lines = out.split("\n")
    # The blank context line is now a single space; real content is unchanged.
    assert " extends Node" in lines
    assert lines[lines.index(" extends Node") + 1] == " "
    assert "-var x = 1" in lines
    assert "+var x = 2" in lines


def test_normalize_leaves_headers_and_wellformed_lines_untouched():
    patch = (
        "diff --git a/f.gd b/f.gd\n"
        "index abc..def 100644\n"
        "--- a/f.gd\n"
        "+++ b/f.gd\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )
    # No blank hunk lines -> identical output (idempotent on well-formed patches).
    assert _normalize_hunk_blank_lines(patch) == patch


def test_normalize_does_not_touch_blank_lines_outside_hunks():
    # A blank line between the header block and a hunk (not inside a hunk body)
    # must not be turned into " ".
    patch = (
        "diff --git a/f.gd b/f.gd\n"
        "--- a/f.gd\n"
        "+++ b/f.gd\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )
    assert _normalize_hunk_blank_lines(patch) == patch


def test_apply_patch_succeeds_with_empty_context_lines(tmp_path):
    # End-to-end: a diff whose blank context lines lost their leading space (as
    # LLM/editor-produced patches often do) must still apply. Without the
    # normalization git rejects it with "patch does not apply".
    baseline = tmp_path / "baseline"
    (baseline / "scripts").mkdir(parents=True)
    # A file WITH a blank line in the middle, so the patch needs a blank context line.
    (baseline / "scripts" / "main.gd").write_text(
        "extends Node\n\nvar x = 1\n", encoding="utf-8")
    patch = (
        "diff --git a/scripts/main.gd b/scripts/main.gd\n"
        "--- a/scripts/main.gd\n"
        "+++ b/scripts/main.gd\n"
        "@@ -1,3 +1,3 @@\n"
        " extends Node\n"
        "\n"                        # empty context line for the blank middle line
        "-var x = 1\n"
        "+var x = 2\n"
    )
    with folder_workspace(baseline) as ws:
        apply_patch(ws, patch)  # must not raise
        assert "var x = 2" in (ws / "scripts" / "main.gd").read_text(encoding="utf-8")
