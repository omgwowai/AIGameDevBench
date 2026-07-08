from __future__ import annotations

import json
from pathlib import Path

from aigamedevbench.driver import NoOpDriver, PatchDriver
from aigamedevbench.runner import run_testcase
from aigamedevbench.testcase import Testcase
from aigamedevbench.git_ops import git_run


def _make_repo_with_config(tmp_path) -> tuple[Path, str]:
    repo = tmp_path / "repo"; repo.mkdir()
    git_run(["init"], cwd=repo)
    git_run(["config", "user.email", "t@t.com"], cwd=repo)
    git_run(["config", "user.name", "t"], cwd=repo)
    (repo / "data").mkdir()
    (repo / "data" / "char.json").write_text(json.dumps({"attack": 50}) + "\n", encoding="utf-8")
    git_run(["add", "."], cwd=repo)
    git_run(["commit", "-m", "baseline"], cwd=repo)
    head = git_run(["rev-parse", "HEAD"], cwd=repo).strip()
    return repo, head


def _config_testcase(tmp_path, head) -> Testcase:
    tc_dir = tmp_path / "tc"; tc_dir.mkdir()
    (tc_dir / "expected.json").write_text(json.dumps({"fields": [
        {"name": "attack", "aliases": ["attack"], "files_glob": "**/*.json",
         "expected": 60, "tol": 1e-6, "base": 50, "must_differ_from_base": True},
    ]}), encoding="utf-8")
    return Testcase("bench-x", "intent_translation", head,
                    "Set attack to base+20%", "py_config", "verify.py", "fields", tc_dir)


def test_noop_driver_fails_intent(tmp_path):
    repo, head = _make_repo_with_config(tmp_path)
    tc = _config_testcase(tmp_path, head)
    result = run_testcase(repo, tc, NoOpDriver(), "noop", config={})
    assert result.score == 0.0
    assert result.verifier_result.status in ("fail", "partial")


def test_correct_patch_passes(tmp_path):
    repo, head = _make_repo_with_config(tmp_path)
    tc = _config_testcase(tmp_path, head)
    patch = (
        "diff --git a/data/char.json b/data/char.json\n"
        "--- a/data/char.json\n"
        "+++ b/data/char.json\n"
        "@@ -1 +1 @@\n"
        '-{"attack": 50}\n'
        '+{"attack": 60}\n'
    )
    result = run_testcase(repo, tc, PatchDriver(patch), "patch", config={})
    assert result.score == 1.0
    assert result.verifier_result.status == "pass"


def _folder_testcase(tmp_path) -> Testcase:
    """A folder-type testcase using the pure-Python py_config verifier, so the
    runner's folder_workspace branch can be exercised without a Godot binary."""
    tc_dir = tmp_path / "tc-folder"
    baseline = tc_dir / "baseline"
    (baseline / "data").mkdir(parents=True)
    (baseline / "data" / "char.json").write_text(json.dumps({"attack": 50}) + "\n", encoding="utf-8")
    (tc_dir / "expected.json").write_text(json.dumps({"fields": [
        {"name": "attack", "aliases": ["attack"], "files_glob": "**/*.json",
         "expected": 60, "tol": 1e-6, "base": 50, "must_differ_from_base": True},
    ]}), encoding="utf-8")
    return Testcase("gdb-x", "behavior_logic", "", "task", "py_config",
                    "expected.json", "fields", tc_dir, source_kind="folder")


def test_folder_testcase_uses_folder_workspace(tmp_path):
    tc = _folder_testcase(tmp_path)
    # repo_root is None: folder-type must not need a game repo.
    result = run_testcase(None, tc, NoOpDriver(), "noop", config={})
    assert result.score == 0.0

    patch = (
        "diff --git a/data/char.json b/data/char.json\n"
        "--- a/data/char.json\n"
        "+++ b/data/char.json\n"
        "@@ -1 +1 @@\n"
        '-{"attack": 50}\n'
        '+{"attack": 60}\n'
    )
    result = run_testcase(None, tc, PatchDriver(patch), "patch", config={})
    assert result.score == 1.0
    assert result.verifier_result.status == "pass"


def test_git_testcase_uses_manifest_source_repo(tmp_path):
    repo, head = _make_repo_with_config(tmp_path)
    tc = _config_testcase(tmp_path, head)
    tc.source_repo = str(repo)
    # repo_root is None: source_repo in the manifest should make exported Survey
    # testcases runnable from the Bench repo instead of the game repo.
    result = run_testcase(None, tc, NoOpDriver(), "noop", config={})
    assert result.testcase_id == "bench-x"
    assert result.score == 0.0


_CHAR_PATCH = (
    "diff --git a/data/char.json b/data/char.json\n"
    "--- a/data/char.json\n"
    "+++ b/data/char.json\n"
    "@@ -1 +1 @@\n"
    '-{"attack": 50}\n'
    '+{"attack": 60}\n'
)


def test_run_captures_diff(tmp_path):
    tc = _folder_testcase(tmp_path)
    result = run_testcase(None, tc, PatchDriver(_CHAR_PATCH), "patch", config={})
    # The harness's change is captured on the result for logging/reporting.
    assert '+{"attack": 60}' in result.diff
    assert "data/char.json" in result.diff


def test_run_saves_artifacts(tmp_path):
    tc = _folder_testcase(tmp_path)
    art = tmp_path / "artifacts"
    result = run_testcase(None, tc, PatchDriver(_CHAR_PATCH), "patch", config={},
                          artifacts_dir=art)
    dest = art / "gdb-x"
    # A diff file and a copy of the post-edit changed file are persisted.
    assert (dest / "changes.diff").read_text(encoding="utf-8").strip() != ""
    saved = dest / "files" / "data" / "char.json"
    assert saved.exists()
    assert '"attack": 60' in saved.read_text(encoding="utf-8")
    assert result.artifacts_path == str(dest)


def test_gate_failure_carries_l0_l1_details(tmp_path):
    # A failed L0/L1 gate must explain WHY in the error (the l0/l1 details), not
    # just say "L0/L1 gate failed". Otherwise the JSON report is undebuggable —
    # exactly the case where report.json showed a bare gate failure with no reason.
    tc = _folder_testcase(tmp_path)
    # Add a .gd file with an unclosed paren so the L0 syntax check fails the gate.
    bad_patch = (
        "diff --git a/broken.gd b/broken.gd\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/broken.gd\n"
        "@@ -0,0 +1,3 @@\n"
        "+extends Node\n"
        "+func f():\n"
        "+\tvar x = foo(1, 2\n"
    )
    result = run_testcase(None, tc, PatchDriver(bad_patch), "patch", config={})
    assert result.score == 0.0
    assert result.verifier_result.status == "fail"
    err = result.verifier_result.error
    assert "L0/L1 gate failed" in err
    assert "unclosed" in err and "broken.gd" in err


def test_noop_run_has_empty_diff_and_no_artifacts(tmp_path):
    tc = _folder_testcase(tmp_path)
    art = tmp_path / "artifacts"
    result = run_testcase(None, tc, NoOpDriver(), "noop", config={},
                          artifacts_dir=art)
    # Doing nothing produces no diff; the changes.diff is written but empty.
    assert result.diff.strip() == ""
    assert (art / "gdb-x" / "changes.diff").read_text(encoding="utf-8").strip() == ""

def test_run_uses_fixed_attempt_workspace(tmp_path):
    tc = _folder_testcase(tmp_path)
    attempt_dir = tmp_path / "attempt"
    seen = {}

    class RecordingDriver:
        def run(self, task, workspace):
            seen["workspace"] = workspace
            seen["task"] = task
            (workspace / "TASK.md").write_text(task, encoding="utf-8")
            (workspace / "data" / "char.json").write_text(
                json.dumps({"attack": 60}) + "\n", encoding="utf-8"
            )

    result = run_testcase(
        None,
        tc,
        RecordingDriver(),
        "recording",
        config={},
        workspace_root=attempt_dir,
        workspace_name="workspace",
        keep_workspace=True,
    )

    assert result.score == 1.0
    assert seen["workspace"] == attempt_dir / "workspace"
    assert (attempt_dir / "workspace" / "TASK.md").read_text(encoding="utf-8") == "task"


# --- P0: structured failure_stage + per-stage timings ---

def test_noop_failure_stage_is_no_change(tmp_path):
    # A run that changed nothing must be labeled no_change even though a py_config
    # noop passes the L0/L1 gate (no .tscn to reject) and only fails at scoring —
    # the funnel must distinguish "did no work" from a real capability gap.
    tc = _folder_testcase(tmp_path)
    result = run_testcase(None, tc, NoOpDriver(), "noop", config={})
    assert result.failure_stage == "no_change"
    assert result.score == 0.0


def test_correct_patch_failure_stage_is_none(tmp_path):
    tc = _folder_testcase(tmp_path)
    result = run_testcase(None, tc, PatchDriver(_CHAR_PATCH), "patch", config={})
    # A real change that the verifier scored is stage none (not a pipeline failure).
    assert result.failure_stage == "none"
    assert result.score == 1.0


def test_gate_failure_stage_is_l0(tmp_path):
    tc = _folder_testcase(tmp_path)
    bad_patch = (
        "diff --git a/broken.gd b/broken.gd\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/broken.gd\n"
        "@@ -0,0 +1,3 @@\n"
        "+extends Node\n"
        "+func f():\n"
        "+\tvar x = foo(1, 2\n"
    )
    result = run_testcase(None, tc, PatchDriver(bad_patch), "patch", config={})
    # A changed-but-rejected run is an l0 failure, distinct from no_change.
    assert result.failure_stage == "l0"
    assert result.score == 0.0


def test_run_records_stage_timings(tmp_path):
    tc = _folder_testcase(tmp_path)
    result = run_testcase(None, tc, PatchDriver(_CHAR_PATCH), "patch", config={})
    t = result.timings
    # Every pipeline stage is timed so the report can attribute cost, not just
    # the harness wall_time. All are non-negative numbers.
    for key in ("import_ms", "l0_ms", "l1_ms", "validation_ms", "verifier_ms", "total_ms"):
        assert key in t, f"missing timing {key}"
        assert isinstance(t[key], (int, float)) and t[key] >= 0.0
