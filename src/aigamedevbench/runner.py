from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
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
    # Which pipeline layer explains a non-perfect run, for failure-funnel analysis:
    #   none          -> gate passed and verifier ran (score may still be < 1:
    #                    that's a capability gap, not a pipeline failure)
    #   no_change     -> harness produced no diff at all
    #   harness_error -> command harness timed out / stalled / blocked / exited != 0
    #                    (filled in by the CLI, which owns the driver outcome)
    #   l0 / l1       -> the L0/L1 admission gate rejected the change
    #   verifier      -> gate passed but the verifier itself errored
    failure_stage: str = "none"
    # Per-stage wall time (ms): import_ms/l0_ms/l1_ms/validation_ms from the gate,
    # plus verifier_ms and total_ms measured here.
    timings: dict = field(default_factory=dict)
    # The command driver's per-run outcome (exit_code, wall_time, timed_out,
    # stalled, blocked_on_approval, log_path, ai_agent_context), captured here so
    # it travels WITH the result. Reading driver.last_outcome after the fact is a
    # race once runs go parallel; carrying it on the result is thread-safe.
    # Empty dict for noop/patch drivers.
    harness_outcome: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "testcase_id": self.testcase_id,
            "harness": self.harness,
            "category": self.category,
            "l0_l1_pass": self.l0_l1_pass,
            "score": self.score,
            "failure_stage": self.failure_stage,
            "timings": self.timings,
            "verifier_result": self.verifier_result.to_dict(),
            "diff": self.diff,
            "artifacts_path": self.artifacts_path,
        }
        # Merge the command driver's outcome (exit_code / wall_time / stalled /
        # blocked_on_approval / log_path / ai_agent_context) into the record so
        # the report and dashboard see it. Absent for noop/patch drivers.
        if self.harness_outcome:
            d.update(self.harness_outcome)
        # harness_ms mirrors wall_time into the timings block so the timing view
        # can attribute harness cost alongside the pipeline stages.
        if isinstance(self.harness_outcome.get("wall_time"), (int, float)):
            d["timings"] = {**self.timings,
                            "harness_ms": round(self.harness_outcome["wall_time"] * 1000.0, 1)}
        # Surface the harness's worst single turn (its internal bottleneck) into
        # the timings block, alongside the pipeline stages, so the slowest step
        # is comparable with import/L0/verify cost.
        ctx = self.harness_outcome.get("ai_agent_context")
        if isinstance(ctx, dict):
            slowest = ctx.get("slowest_turn")
            if isinstance(slowest, dict) and isinstance(slowest.get("duration_ms"), (int, float)):
                d["timings"] = {**d.get("timings", self.timings),
                                "slowest_turn_ms": slowest["duration_ms"]}
        return d


@contextmanager
def _workspace_for(repo_root: Path | None, testcase: Testcase,
                   workspace_root: Path | str | None = None,
                   workspace_name: str | None = None,
                   keep_workspace: bool = False):
    if testcase.source_kind == "folder":
        if testcase.snapshot:
            baseline_dir = testcase.dir.parent / "_snapshots" / testcase.snapshot
            if not baseline_dir.is_dir() or not any(baseline_dir.iterdir()):
                raise FileNotFoundError(
                    f"testcase '{testcase.id}' references snapshot "
                    f"'{testcase.snapshot}' which is not present at {baseline_dir}. "
                    f"Snapshots are generated on demand (not committed) - run:\n"
                    f"    python3 scripts/make_snapshots.py "
                    f"--testcases-dir {testcase.dir.parent}"
                )
        else:
            baseline_dir = testcase.dir / "baseline"
        with folder_workspace(
            baseline_dir,
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
    run_start = time.perf_counter()
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
        harness_outcome = dict(getattr(driver, "last_outcome", None) or {})
        harness_failed = bool(
            harness_outcome.get("timed_out") or harness_outcome.get("stalled")
            or harness_outcome.get("blocked_on_approval")
            or harness_outcome.get("completed_but_hung")
            or (harness_outcome.get("exit_code", 0) not in (0, None)))

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
        # Base timings from the gate; verifier_ms/total_ms are added below so a
        # gate rejection still records where its time went.
        timings = dict(verification.timings)

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
            # A hard harness failure takes precedence: the empty/partial diff is
            # a symptom of the harness dying, not a gate reject. Otherwise
            # distinguish "changed nothing" (no_change) from "changed something
            # the gate rejected" (l0/l1) — same 0 score, different failure modes.
            if harness_failed:
                stage = "harness_error"
            elif not changed_files:
                stage = "no_change"
            elif not verification.l0_pass:
                stage = "l0"
            else:
                stage = "l1"
            timings["verifier_ms"] = 0.0
            timings["total_ms"] = round((time.perf_counter() - run_start) * 1000.0, 1)
            return RunResult(testcase.id, harness_id, testcase.category,
                             False, failed, 0.0, diff=diff,
                             artifacts_path=artifacts_path,
                             failure_stage=stage, timings=timings,
                             harness_outcome=harness_outcome)

        verifier = get_verifier(testcase.verifier_type)
        godot_binary = config.get("global", {}).get("godot", {}).get("binary", "godot")
        verify_start = time.perf_counter()
        vr = verifier.verify(testcase, workspace, godot_binary)
        timings["verifier_ms"] = round((time.perf_counter() - verify_start) * 1000.0, 1)
        score = 0.0 if vr.status == "error" else vr.score
        # Failure-stage precedence: a hard harness failure wins (rare here — the
        # gate usually catches it first — but a non-zero exit with a valid diff
        # can reach this branch). Then no_change (did no work), then a verifier
        # error. Otherwise the verifier ran on a real change: stage `none`
        # (score may still be < 1 — a capability gap, not a pipeline failure).
        if harness_failed:
            stage = "harness_error"
        elif not changed_files:
            stage = "no_change"
        elif vr.status == "error":
            stage = "verifier"
        else:
            stage = "none"
        timings["total_ms"] = round((time.perf_counter() - run_start) * 1000.0, 1)
        return RunResult(testcase.id, harness_id, testcase.category,
                         True, vr, score, diff=diff,
                         artifacts_path=artifacts_path,
                         failure_stage=stage, timings=timings,
                         harness_outcome=harness_outcome)


# The command driver writes the task prompt into the workspace as TASK.md; it is
# a benchmark artifact, not the harness's game-dev change. Excluding it keeps the
# diff clean AND makes "harness did nothing" register as no_change (otherwise the
# lone TASK.md would mask a no-op harness as having changed something).
_BENCH_ARTIFACTS = {"TASK.md"}


def _list_changed(workspace: Path) -> list[str]:
    from aigamedevbench.git_ops import git_run
    out = git_run(["status", "--porcelain"], cwd=workspace)
    files = []
    for line in out.splitlines():
        if len(line) > 3:
            rel = line[3:].strip()
            if rel in _BENCH_ARTIFACTS:
                continue
            files.append(rel)
    return files


def capture_diff(workspace: Path) -> str:
    """Unified diff of the harness's changes against the committed baseline.

    Includes untracked files (--no-index would miss them); `git add -N` registers
    them as intent-to-add so `git diff` emits their full content as additions.
    The benchmark's own TASK.md is excluded so the diff shows only the harness's
    game-dev change."""
    from aigamedevbench.git_ops import git_run
    try:
        git_run(["add", "-A", "-N"], cwd=workspace)
    except Exception:
        pass
    # ':(exclude)TASK.md' drops the prompt file the driver wrote; the rest of the
    # working tree diffs as normal.
    return git_run(["diff", "--", ".", ":(exclude)TASK.md"], cwd=workspace)


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
