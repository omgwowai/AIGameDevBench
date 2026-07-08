from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from aigamedevbench.driver import HarnessDriver
from aigamedevbench.result import VerifierResult
from aigamedevbench.testcase import Testcase
from aigamedevbench.verifiers.base import get_verifier
from aigamedevbench.workspace import isolated_workspace, folder_workspace
from aigamedevbench.validation import run_validation
from contextlib import contextmanager

# Import verifiers package so all @register decorators run.
import aigamedevbench.verifiers  # noqa: F401


@dataclass
class RunResult:
    testcase_id: str
    harness: str
    category: str
    l0_l1_pass: bool
    verifier_result: VerifierResult
    score: float
    diff: str = ""
    artifacts_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "testcase_id": self.testcase_id,
            "harness": self.harness,
            "category": self.category,
            "l0_l1_pass": self.l0_l1_pass,
            "score": self.score,
            "verifier_result": self.verifier_result.to_dict(),
            "diff": self.diff,
            "artifacts_path": self.artifacts_path,
        }


@contextmanager
def _workspace_for(repo_root: Path | None, testcase: Testcase,
                   workspace_root: Path | str | None = None,
                   workspace_name: str | None = None,
                   keep_workspace: bool = False):
    if testcase.source_kind == "folder":
        with folder_workspace(
            testcase.dir / "baseline",
            workspace_root,
            workspace_name=workspace_name,
            keep_workspace=keep_workspace,
        ) as ws:
            yield ws
    else:
        source_repo = Path(testcase.source_repo) if testcase.source_repo else repo_root
        if source_repo is None:
            raise ValueError(f"git-type testcase '{testcase.id}' requires a repo root")
        with isolated_workspace(
            source_repo,
            testcase.baseline_ref,
            workspace_root,
            workspace_name=workspace_name,
            keep_workspace=keep_workspace,
        ) as ws:
            yield ws


def run_testcase(repo_root: Path | None, testcase: Testcase, driver: HarnessDriver,
                 harness_id: str, config: dict | None = None,
                 workspace_root: Path | str | None = None,
                 artifacts_dir: Path | str | None = None,
                 workspace_name: str | None = None,
                 keep_workspace: bool = False,
                 on_state: Callable[[str], None] | None = None) -> RunResult:
    config = config or {}
    with _workspace_for(
        repo_root,
        testcase,
        workspace_root,
        workspace_name=workspace_name,
        keep_workspace=keep_workspace,
    ) as workspace:
        driver.run(testcase.task, workspace)
        if on_state is not None:
            on_state("verifying")

        changed_files = _list_changed(workspace)
        # Snapshot the harness's work BEFORE validation runs: godot_import writes
        # a .godot/ cache into the workspace, which would pollute both the diff
        # and the saved tree. Capturing here preserves exactly what the harness
        # produced, for later re-verification and for logging the change.
        diff = capture_diff(workspace)
        artifacts_path = None
        if artifacts_dir is not None:
            artifacts_path = _save_artifacts(
                Path(artifacts_dir), testcase.id, workspace, diff, changed_files)

        verification = run_validation(workspace, changed_files, config)
        gate_pass = verification.l0_pass and verification.l1_pass

        if not gate_pass:
            # Carry the l0/l1 reasons in the error so a JSON report explains WHY
            # the gate failed, not just that it did. A bare "L0/L1 gate failed"
            # is undebuggable from the report alone.
            reasons = [*verification.l0_details, *verification.l1_details]
            error = "L0/L1 gate failed"
            if reasons:
                error += ": " + "; ".join(reasons)
            failed = VerifierResult(score=0.0, status="fail", checks=[],
                                    category=testcase.category,
                                    error=error)
            return RunResult(testcase.id, harness_id, testcase.category,
                             False, failed, 0.0, diff=diff,
                             artifacts_path=artifacts_path)

        verifier = get_verifier(testcase.verifier_type)
        godot_binary = config.get("global", {}).get("godot", {}).get("binary", "godot")
        vr = verifier.verify(testcase, workspace, godot_binary)
        score = 0.0 if vr.status == "error" else vr.score
        return RunResult(testcase.id, harness_id, testcase.category,
                         True, vr, score, diff=diff,
                         artifacts_path=artifacts_path)


def _list_changed(workspace: Path) -> list[str]:
    from aigamedevbench.git_ops import git_run
    out = git_run(["status", "--porcelain"], cwd=workspace)
    files = []
    for line in out.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip())
    return files


def capture_diff(workspace: Path) -> str:
    """Unified diff of the harness's changes against the committed baseline.

    Includes untracked files (--no-index would miss them); `git add -N` registers
    them as intent-to-add so `git diff` emits their full content as additions."""
    from aigamedevbench.git_ops import git_run
    try:
        git_run(["add", "-A", "-N"], cwd=workspace)
    except Exception:
        pass
    return git_run(["diff"], cwd=workspace)


def _save_artifacts(artifacts_dir: Path, testcase_id: str, workspace: Path,
                    diff_text: str, changed_files: list[str]) -> str | None:
    """Persist the harness's output for later re-verification: a unified diff and
    a copy of every changed file's post-edit content. Returns the destination dir
    (str) or None on failure (artifact capture must never abort a run)."""
    try:
        dest = artifacts_dir / testcase_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "changes.diff").write_text(diff_text, encoding="utf-8")
        files_root = dest / "files"
        for rel in changed_files:
            src = workspace / rel
            if not src.is_file():
                continue  # deleted or a directory entry: the diff already records it
            out = files_root / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
        return str(dest)
    except Exception:
        return None
