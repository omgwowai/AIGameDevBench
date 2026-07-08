from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import click

from aigamedevbench.git_ops import get_repo_root
from aigamedevbench.codex_harness import (
    CodexHarnessRuntime,
    materialize_attempt_codex_runtime,
    materialize_codex_harness,
)
from aigamedevbench.driver import CommandHarnessDriver
from aigamedevbench.execution_config import ExecutionConfig, load_execution_config
from aigamedevbench.experiment_config import (
    COMPONENT_KEYS,
    ExperimentCell,
    load_experiment_file,
)

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency is declared.
    yaml = None


DEFAULT_TIMEOUT = 1200.0
_OUTPUT_LOCK = threading.Lock()


def _safe_path_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in value) or "default"


def _json_default(value: Any) -> str:
    return str(value)


class _JsonlEventWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: dict[str, Any]) -> None:
        payload = {
            "ts": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            **event,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")


def _config(godot_binary: str) -> dict:
    return {"global": {"godot": {"binary": godot_binary}}}


def _echo_harness_failure(testcase_id: str, outcome: dict, tail_lines: int = 30) -> None:
    """Print a harness failure (timeout, stall, approval-block, or non-zero exit)
    and its log tail to the screen so problems are visible immediately instead of
    buried in a log file."""
    timed_out = outcome.get("timed_out")
    stalled = outcome.get("stalled")
    blocked = outcome.get("blocked_on_approval")
    exit_code = outcome.get("exit_code", 0)
    if not (timed_out or stalled or blocked) and exit_code == 0:
        return
    if blocked:
        reason = "BLOCKED ON APPROVAL"
    elif stalled:
        reason = "STALLED (no output)"
    elif timed_out:
        reason = "TIMEOUT"
    else:
        reason = f"exit_code={exit_code}"
    click.echo(f"  !! harness {reason} for {testcase_id}", err=True)
    log_path = outcome.get("log_path")
    if not log_path:
        click.echo("     (no log captured - run with --log-dir to capture output)", err=True)
        return
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        click.echo(f"     (could not read log {log_path}: {e})", err=True)
        return
    lines = text.splitlines()
    click.echo(f"     log: {log_path}", err=True)
    for line in lines[-tail_lines:]:
        click.echo(f"     | {line}", err=True)


def _echo_verifier_result(verifier_result, tail_detail: int = 200) -> None:
    """Print the verifier's per-check breakdown (and any error) to the screen so
    the user can see *why* a testcase scored what it did, not just the score."""
    if verifier_result.error:
        click.echo(f"     verifier error: {verifier_result.error}", err=True)
    for c in verifier_result.checks:
        mark = "PASS" if c.passed else "FAIL"
        line = f"     [{mark}] {c.name}"
        detail = (c.detail or "").strip()
        if detail:
            if len(detail) > tail_detail:
                detail = detail[:tail_detail] + "..."
            line += f" - {detail}"
        if c.expected is not None or c.actual is not None:
            line += f" (expected: {c.expected!r}, actual: {c.actual!r})"
        click.echo(line, err=True)


def _echo_diff(result, max_lines: int = 40) -> None:
    """Log what the harness changed: a per-file summary plus a capped preview of
    the diff, so the modification is visible on screen and in the log without
    dumping a huge patch. The full diff lives in the report and --artifacts-dir."""
    diff = result.diff or ""
    if not diff.strip():
        click.echo("     changes: (none)", err=True)
        return
    files = [ln[len("+++ b/"):] for ln in diff.splitlines()
             if ln.startswith("+++ b/") and not ln.endswith("/dev/null")]
    if files:
        click.echo(f"     changed {len(files)} file(s): {', '.join(files)}", err=True)
    lines = diff.splitlines()
    click.echo("     --- diff ---", err=True)
    for ln in lines[:max_lines]:
        click.echo(f"     | {ln}", err=True)
    if len(lines) > max_lines:
        click.echo(f"     | ... ({len(lines) - max_lines} more lines)", err=True)
    if result.artifacts_path:
        click.echo(f"     saved: {result.artifacts_path}", err=True)


@click.group()
@click.version_option()
def main():
    """AIGameDevBench: controlled benchmark for AI Godot game development."""
    pass


@main.command("list")
@click.option("--testcases-dir", default="./testcases", type=click.Path(exists=True),
              help="Directory of testcases (default: ./testcases)")
def list_cmd(testcases_dir: str):
    """List discovered testcases."""
    from aigamedevbench.testcase import discover_testcases

    for tc in discover_testcases(Path(testcases_dir)):
        click.echo(f"{tc.id}\t{tc.category}\t{tc.verifier_type}\t{tc.source_kind}")


@main.command("audit")
@click.option("--testcases-dir", default="./testcases", type=click.Path(exists=True),
              help="Directory of testcases (default: ./testcases)")
@click.option("--only", "only", multiple=True,
              help="Audit only this testcase id (repeatable)")
@click.option("--json", "json_file", default=None, type=click.Path(),
              help="Write a machine-readable health snapshot")
@click.option("--repo-root", default=".", type=click.Path(),
              help="Fallback repo for git-type cases without source_repo")
@click.option("--godot-binary", default="godot",
              help="Godot executable for runtime verifiers")
def audit_cmd(testcases_dir: str, only: tuple[str, ...], json_file: str | None,
              repo_root: str, godot_binary: str):
    """Audit testcase health: noop must score 0 and golden patch must score 1."""
    from aigamedevbench.testcase_audit import (
        audit_testcases, format_audit_table, select_testcases, write_health_json,
    )

    rows = audit_testcases(
        select_testcases(Path(testcases_dir).resolve(), only or None),
        Path(repo_root).resolve(),
        godot_binary,
    )
    click.echo(format_audit_table(rows))
    if json_file:
        write_health_json(Path(json_file), rows)
        click.echo(f"--- health written to {json_file}")
    if any(not row["healthy"] for row in rows):
        raise click.exceptions.Exit(1)


@main.command("smoke")
@click.option("--testcases-dir", default="./testcases", type=click.Path(exists=True),
              help="Directory of testcases (default: ./testcases)")
@click.option("--testcase", "testcase_id", required=True,
              help="Testcase id to validate")
@click.option("--patch", "patch_file", default=None, type=click.Path(exists=True),
              help="Golden patch to replay (default: testcase/fix.diff)")
@click.option("--repo-root", default=".", type=click.Path(),
              help="Fallback repo for git-type cases without source_repo")
@click.option("--godot-binary", default="godot",
              help="Godot executable for runtime verifiers")
def smoke_cmd(testcases_dir: str, testcase_id: str, patch_file: str | None,
              repo_root: str, godot_binary: str):
    """Quickly validate one testcase's admission invariants."""
    from aigamedevbench.testcase import discover_testcases
    from aigamedevbench.testcase_audit import audit_one, format_audit_table

    matches = [tc for tc in discover_testcases(Path(testcases_dir).resolve())
               if tc.id == testcase_id]
    if not matches:
        click.echo(f"Testcase '{testcase_id}' not found.")
        raise click.exceptions.Exit(1)
    row = audit_one(matches[0], Path(repo_root).resolve(), godot_binary,
                    Path(patch_file) if patch_file else None)
    click.echo(format_audit_table([row]))
    if not row["healthy"]:
        raise click.exceptions.Exit(1)


@main.command("probe-permissions")
@click.option("--harness-cmd", "harness_cmd", required=True,
              help="Command template to probe, e.g. 'codex exec --cd {workspace} {task}'")
@click.option("--probe-root", default=".aigdbench-permission-probe", type=click.Path(),
              help="Directory where probe workspace, canaries, logs, and JSON result are written")
@click.option("--timeout", default=120.0, type=float, help="Probe harness timeout (s)")
@click.option("--stall-timeout", "stall_timeout", default=0.0, type=float,
              help="Abort if the probe produces no output for this many seconds; default disabled")
@click.option("--stream/--no-stream", "stream", default=True,
              help="Stream probe harness output live to the screen")
def probe_permissions_cmd(
    harness_cmd: str,
    probe_root: str,
    timeout: float,
    stall_timeout: float,
    stream: bool,
) -> None:
    """Verify a harness can write the test workspace but not outside canary dirs."""
    from aigamedevbench.permission_probe import run_permission_probe

    on_line = None
    if stream:
        def on_line(line: str) -> None:
            click.echo(f"  [permission-probe] {line}", err=True)

    result = run_permission_probe(
        harness_cmd,
        Path(probe_root),
        timeout=timeout,
        stall_timeout=stall_timeout,
        on_line=on_line,
    )
    status = "PASS" if result["passed"] else "FAIL"
    click.echo(
        f"{status} permission probe: "
        f"inside_write_ok={result['inside_write_ok']} "
        f"outside_read_blocked={result['outside_read_blocked']} "
        f"outside_write_blocked={result['outside_write_blocked']}"
    )
    click.echo(f"--- permission probe written to {result['result_path']}")
    if not result["passed"]:
        raise click.exceptions.Exit(1)


@main.command("scaffold")
@click.option("--testcases-dir", default="./testcases", type=click.Path(),
              help="Directory where the testcase will be created (default: ./testcases)")
@click.option("--id", "testcase_id", required=True,
              help="New testcase id, e.g. pathfinding-npc-bridge-astar")
@click.option("--category", default="behavior_logic",
              type=click.Choice([
                  "behavior_logic", "intent_translation", "precise_edit",
                  "architecture", "visual_audio",
              ]))
@click.option("--task", default="TODO: describe the requested game-dev change",
              help="Task text to put in testcase.toml")
@click.option("--source-project", default=None, type=click.Path(exists=True),
              help="Copy this Godot project into baseline/ (strips .git/.godot)")
@click.option("--source-repo", default=None,
              help="Provenance label or repo path recorded in testcase.toml")
@click.option("--force", is_flag=True,
              help="Overwrite scaffold files if they already exist")
def scaffold_cmd(testcases_dir: str, testcase_id: str, category: str, task: str,
                 source_project: str | None, source_repo: str | None, force: bool):
    """Create a standard self-contained folder-type testcase skeleton."""
    from aigamedevbench.testcase_scaffold import scaffold_folder_testcase

    try:
        result = scaffold_folder_testcase(
            Path(testcases_dir), testcase_id, category=category, task=task,
            source_project=Path(source_project) if source_project else None,
            source_repo=source_repo, force=force,
        )
    except (OSError, ValueError) as e:
        click.echo(str(e))
        raise click.exceptions.Exit(1)
    click.echo(f"--- created {result.testcase_dir}")
    for path in result.created_files:
        click.echo(f"  {path}")


def _harness_metadata(cell: ExperimentCell | None) -> dict:
    if cell is None:
        return {}
    metadata = {
        "experiment_id": cell.experiment_id,
        "cell_id": cell.cell_id,
        "model": cell.model,
        "model_reasoning_effort": cell.model_reasoning_effort,
        "agent_cli": cell.agent_cli,
        "orchestration": cell.orchestration.to_dict(),
        "orchestration_id": cell.orchestration.id,
        "orchestration_source_type": cell.orchestration.source_type,
        "task_set": cell.task_set,
        "harness_preset": cell.harness.preset,
        "harness_target_agent_cli": cell.harness.target_agent_cli,
        "harness_components": cell.harness.components,
        "harness_fingerprint": cell.harness.fingerprint,
    }
    for key in COMPONENT_KEYS:
        metadata[f"harness_{key}"] = cell.harness.components.get(key, [])
    return metadata


def _runner_version() -> str:
    try:
        return version("aigamedevbench")
    except PackageNotFoundError:
        return "0.1.0"


def _benchmark_git_commit() -> str | None:
    from aigamedevbench.git_ops import git_run

    repo_root = Path(__file__).resolve().parents[2]
    try:
        return git_run(["rev-parse", "HEAD"], cwd=repo_root).strip()
    except Exception:
        return None


def _new_run_metadata() -> dict[str, Any]:
    return {
        "git_commit": _benchmark_git_commit(),
        "runner_version": _runner_version(),
        "started_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _write_manifest(
    cell: ExperimentCell,
    cell_dir: Path,
    testcases: list,
    run_metadata: dict[str, Any] | None = None,
    codex_runtime: dict[str, Any] | None = None,
    execution_config: ExecutionConfig | None = None,
) -> None:
    manifest = {
        **_harness_metadata(cell),
        **(run_metadata or {}),
        "testcases_dir": str(Path(cell.testcases_dir)),
        "testcase_ids": [tc.id for tc in testcases],
        "testcase_timeouts": {
            tc.id: (cell.testcase_timeouts or {})[tc.id]
            for tc in testcases
            if tc.id in (cell.testcase_timeouts or {})
        },
    }
    if execution_config is not None:
        manifest["execution_config"] = {
            "experiment_jobs": execution_config.experiment_jobs,
            "testcase_jobs": execution_config.testcase_jobs,
            "max_jobs": execution_config.max_jobs,
        }
    if codex_runtime is not None:
        manifest["codex_harness_runtime"] = codex_runtime
    cell_dir.mkdir(parents=True, exist_ok=True)
    (cell_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def _csv_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _write_final_results_csv(path: Path, report: dict[str, Any]) -> None:
    columns = [
        "experiment_id",
        "cell_id",
        "model",
        "agent_cli",
        "model_reasoning_effort",
        "orchestration_id",
        "orchestration_source_type",
        "task_set",
        "harness_preset",
        "harness_target_agent_cli",
        "harness_fingerprint",
        *[f"harness_{key}" for key in COMPONENT_KEYS],
        "testcase_id",
        "category",
        "score",
        "status",
        "l0_l1_pass",
        "error",
        "harness",
        "runner_version",
        "git_commit",
        "started_at",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for record in report.get("testcases", []):
            verifier = record.get("verifier_result") or {}
            row = {
                "status": record.get("status") or verifier.get("status"),
                "error": record.get("error") or verifier.get("error"),
            }
            for column in columns:
                if column not in row:
                    row[column] = record.get(column, report.get(column))
            writer.writerow({key: _csv_scalar(row.get(key)) for key in columns})


def _write_report_outputs(
    report: dict[str, Any],
    report_file: str | None,
    cell_dir: Path | None,
) -> None:
    if report_file:
        Path(report_file).write_text(json.dumps(report, indent=2), encoding="utf-8")
        click.echo(f"--- report written to {report_file}")
    if cell_dir is not None:
        cell_dir.mkdir(parents=True, exist_ok=True)
        (cell_dir / "final_results.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        _write_final_results_csv(cell_dir / "final_results.csv", report)


def _make_driver(
    driver: str,
    patch_file: str | None,
    harness_cmd: str | None,
    timeout: float,
    stall_timeout: float,
    log_dir: str,
    stream: bool,
    env: dict[str, str] | None = None,
    log_name: str | None = None,
    completion_probe=None,
):
    from aigamedevbench.driver import NoOpDriver, PatchDriver

    if driver == "patch":
        if not patch_file:
            raise click.ClickException("--patch FILE required with --driver patch")
        return PatchDriver(Path(patch_file).read_text(encoding="utf-8"))
    if driver == "command":
        if not harness_cmd:
            raise click.ClickException("--harness-cmd TEMPLATE required with --driver command")
        on_line = None
        drv = CommandHarnessDriver(
            harness_cmd,
            timeout=timeout,
            log_dir=Path(log_dir),
            stall_timeout=stall_timeout,
            on_line=on_line,
            env=env,
            log_name=log_name,
            completion_probe=completion_probe,
        )
        if stream:
            def on_line(line: str) -> None:
                click.echo(f"  [{drv.label}] {line}", err=True)
            drv.on_line = on_line
        return drv
    return NoOpDriver()


def _attempt_paths(cell_dir: Path | None, testcase_id: str) -> tuple[Path | None, Path | None, Path | None]:
    if cell_dir is None:
        return None, None, None
    attempt_dir = cell_dir / "attempts" / _safe_path_name(testcase_id)
    logs_dir = attempt_dir / "logs"
    return attempt_dir, logs_dir / "events.jsonl", logs_dir / "harness.log"


def _codex_task_complete(codex_home: Path) -> bool:
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.is_dir():
        return False
    for path in sessions_dir.rglob("*.jsonl"):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"type":"task_complete"' in line or '"type": "task_complete"' in line:
                        return True
        except OSError:
            continue
    return False


def _backup_codex_transcripts(
    codex_home: Path,
    destination_dir: Path,
    *,
    cell_id: str,
    testcase_id: str,
) -> list[Path]:
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.is_dir():
        return []
    destination_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    prefix = f"{_safe_path_name(cell_id)}__{_safe_path_name(testcase_id)}"
    for source in sorted(sessions_dir.rglob("*.jsonl")):
        source_digest = hashlib.sha256(str(source.relative_to(sessions_dir)).encode("utf-8")).hexdigest()[:12]
        destination = destination_dir / f"{prefix}__{source_digest}.jsonl"
        if destination.exists():
            stem = destination.stem
            suffix = destination.suffix
            counter = 2
            while True:
                candidate = destination_dir / f"{stem}-{counter}{suffix}"
                if not candidate.exists():
                    destination = candidate
                    break
                counter += 1
        shutil.copy2(source, destination)
        copied.append(destination.resolve())
    return copied


def _run_one_attempt(
    *,
    index: int,
    repo_root: Path | None,
    testcase,
    harness_id: str,
    driver_name: str,
    patch_file: str | None,
    harness_cmd: str | None,
    timeout: float,
    stall_timeout: float,
    log_dir: str,
    stream: bool,
    godot_binary: str,
    workspace_root: str | None,
    artifacts_dir: str | None,
    cell: ExperimentCell | None,
    cell_dir: Path | None,
    metadata: dict[str, Any],
    codex_runtime_obj: CodexHarnessRuntime | None,
    harness_env: dict[str, str] | None,
) -> dict[str, Any]:
    from aigamedevbench.runner import run_testcase
    from aigamedevbench.driver import PatchDriver

    attempt_dir, events_path, attempt_log_path = _attempt_paths(cell_dir, testcase.id)
    if attempt_dir is not None:
        attempt_dir = attempt_dir.resolve()
        events_path = attempt_dir / "logs" / "events.jsonl"
        attempt_log_path = attempt_dir / "logs" / "harness.log"
    event_writer = _JsonlEventWriter(events_path) if events_path is not None else None
    attempt_codex_runtime = materialize_attempt_codex_runtime(codex_runtime_obj, attempt_dir) if attempt_dir else None
    transcript_backup_dir = (
        (cell_dir.resolve().parent / "transcripts")
        if cell_dir is not None and attempt_codex_runtime is not None
        else None
    )
    effective_env = dict(harness_env or {})
    if attempt_codex_runtime is not None:
        effective_env.update(attempt_codex_runtime.env)
    configured_timeout = None
    if (
        cell is not None
        and driver_name == "command"
        and testcase.id in (cell.testcase_timeouts or {})
    ):
        configured_timeout = (cell.testcase_timeouts or {})[testcase.id]
    effective_timeout = configured_timeout if configured_timeout is not None else timeout
    effective_log_dir = str(attempt_log_path.parent) if attempt_log_path is not None else log_dir
    completion_probe = (
        (lambda: _codex_task_complete(attempt_codex_runtime.codex_home))
        if attempt_codex_runtime is not None and driver_name == "command"
        else None
    )
    effective_drv = _make_driver(
        "noop" if driver_name == "patch" and patch_file is None and cell is not None else driver_name,
        patch_file,
        harness_cmd,
        effective_timeout,
        stall_timeout,
        effective_log_dir,
        stream,
        env=effective_env,
        log_name="harness.log" if attempt_log_path is not None and driver_name == "command" else None,
        completion_probe=completion_probe,
    )
    if isinstance(effective_drv, CommandHarnessDriver):
        effective_drv.label = testcase.id
    if driver_name == "patch" and cell is not None and patch_file is None:
        effective_drv = PatchDriver((testcase.dir / "fix.diff").read_text(encoding="utf-8"))

    if event_writer is not None:
        event_writer.write({"type": "state", "state": "running", "testcase_id": testcase.id})
    try:
        def on_state(state: str) -> None:
            if event_writer is not None:
                event_writer.write({"type": "state", "state": state, "testcase_id": testcase.id})

        result = run_testcase(
            repo_root,
            testcase,
            effective_drv,
            harness_id,
            _config(godot_binary),
            workspace_root=(attempt_dir if attempt_dir is not None else workspace_root),
            artifacts_dir=(attempt_dir / "artifacts" if attempt_dir is not None else artifacts_dir),
            workspace_name="workspace" if attempt_dir is not None else None,
            keep_workspace=attempt_dir is not None,
            on_state=on_state,
        )
        record = {**result.to_dict(), **metadata}
        if isinstance(effective_drv, CommandHarnessDriver) and effective_drv.last_outcome is not None:
            record.update(effective_drv.last_outcome)
        if configured_timeout is not None:
            record["configured_timeout"] = configured_timeout
        if attempt_dir is not None:
            record["attempt_dir"] = str(attempt_dir)
            record["workspace_path"] = str(attempt_dir / "workspace")
            record["events_path"] = str(events_path)
            record["log_path"] = str(attempt_log_path)
        if attempt_codex_runtime is not None:
            record["codex_home"] = str(attempt_codex_runtime.codex_home)
        if transcript_backup_dir is not None:
            backup_paths = _backup_codex_transcripts(
                attempt_codex_runtime.codex_home,
                transcript_backup_dir,
                cell_id=cell.cell_id if cell is not None else harness_id,
                testcase_id=testcase.id,
            )
            record["transcript_backup_dir"] = str(transcript_backup_dir.resolve())
            record["transcript_backup_paths"] = [str(path) for path in backup_paths]
        if event_writer is not None:
            event_writer.write({
                "type": "state",
                "state": "finished",
                "testcase_id": testcase.id,
                "score": result.score,
                "status": result.verifier_result.status,
            })
        if attempt_dir is not None:
            (attempt_dir / "result.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False, default=_json_default),
                encoding="utf-8",
            )
        return {"index": index, "result": result, "record": record, "error": None}
    except Exception as e:
        record = {
            "testcase_id": testcase.id,
            "category": testcase.category,
            "score": 0.0,
            "status": "error",
            "error": str(e),
            **metadata,
        }
        if configured_timeout is not None:
            record["configured_timeout"] = configured_timeout
        if attempt_dir is not None:
            record["attempt_dir"] = str(attempt_dir)
            record["workspace_path"] = str(attempt_dir / "workspace")
            record["events_path"] = str(events_path)
            record["log_path"] = str(attempt_log_path)
        if attempt_codex_runtime is not None:
            record["codex_home"] = str(attempt_codex_runtime.codex_home)
        if transcript_backup_dir is not None:
            backup_paths = _backup_codex_transcripts(
                attempt_codex_runtime.codex_home,
                transcript_backup_dir,
                cell_id=cell.cell_id if cell is not None else harness_id,
                testcase_id=testcase.id,
            )
            record["transcript_backup_dir"] = str(transcript_backup_dir.resolve())
            record["transcript_backup_paths"] = [str(path) for path in backup_paths]
        if event_writer is not None:
            event_writer.write({"type": "state", "state": "error", "testcase_id": testcase.id, "error": str(e)})
        if attempt_dir is not None:
            (attempt_dir / "result.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False, default=_json_default),
                encoding="utf-8",
            )
        return {"index": index, "result": None, "record": record, "error": e}


def _run_selected_testcases(
    *,
    testcases_dir: str,
    testcase_id: str | None,
    testcase_ids: list[str] | None,
    harness_id: str,
    driver_name: str,
    patch_file: str | None,
    harness_cmd: str | None,
    timeout: float,
    stall_timeout: float,
    log_dir: str,
    report_file: str | None,
    workspace_root: str | None,
    artifacts_dir: str | None,
    stream: bool,
    godot_binary: str,
    cell: ExperimentCell | None = None,
    cell_dir: Path | None = None,
    run_metadata: dict[str, Any] | None = None,
    codex_runtime: dict[str, Any] | None = None,
    codex_runtime_obj: CodexHarnessRuntime | None = None,
    harness_env: dict[str, str] | None = None,
    execution_config: ExecutionConfig | None = None,
    global_limiter: threading.Semaphore | None = None,
) -> dict:
    from aigamedevbench.testcase import discover_testcases
    from aigamedevbench.runner import run_testcase
    from aigamedevbench.driver import CommandHarnessDriver, PatchDriver

    config = _config(godot_binary)
    initial_driver_name = (
        "noop" if driver_name == "patch" and patch_file is None and cell is not None
        else driver_name
    )
    drv = _make_driver(
        initial_driver_name, patch_file, harness_cmd, timeout, stall_timeout, log_dir, stream,
        env=harness_env,
    )

    testcases = discover_testcases(Path(testcases_dir))
    if testcase_id:
        testcases = [t for t in testcases if t.id == testcase_id]
    if testcase_ids:
        wanted = set(testcase_ids)
        testcases = [t for t in testcases if t.id in wanted]
    if testcase_id and not testcases:
        click.echo(f"Testcase '{testcase_id}' not found.")
        return {"count": 0, "mean_score": 0.0, "testcases": []}

    if cell is not None and cell_dir is not None:
        _write_manifest(cell, cell_dir, testcases, run_metadata, codex_runtime, execution_config)

    if cell is not None and not cell.orchestration.is_supported:
        metadata = {**_harness_metadata(cell), **(run_metadata or {})}
        records = [
            {
                "testcase_id": tc.id,
                "category": tc.category,
                "score": 0.0,
                "status": "unsupported",
                "error": (
                    "Unsupported orchestration: "
                    f"{cell.orchestration.id} ({cell.orchestration.source_type})"
                ),
                **metadata,
            }
            for tc in testcases
        ]
        report = {
            **metadata,
            "harness": harness_id,
            "count": len(testcases),
            "mean_score": 0.0,
            "testcases": records,
        }
        if codex_runtime is not None:
            report["codex_harness_runtime"] = codex_runtime
        _write_report_outputs(report, report_file, cell_dir)
        return report

    needs_repo = any(t.source_kind != "folder" for t in testcases)
    repo_root = None
    if needs_repo:
        try:
            repo_root = get_repo_root(Path.cwd())
        except (RuntimeError, FileNotFoundError):
            repo_root = None

    total = 0.0
    records = []
    metadata = {**_harness_metadata(cell), **(run_metadata or {})}
    if cell_dir is not None:
        ordered_attempts: list[dict[str, Any] | None] = [None] * len(testcases)

        def run_attempt(index: int, tc):
            if global_limiter is not None:
                global_limiter.acquire()
            try:
                return _run_one_attempt(
                    index=index,
                    repo_root=repo_root,
                    testcase=tc,
                    harness_id=harness_id,
                    driver_name=driver_name,
                    patch_file=patch_file,
                    harness_cmd=harness_cmd,
                    timeout=timeout,
                    stall_timeout=stall_timeout,
                    log_dir=log_dir,
                    stream=stream,
                    godot_binary=godot_binary,
                    workspace_root=workspace_root,
                    artifacts_dir=artifacts_dir,
                    cell=cell,
                    cell_dir=cell_dir,
                    metadata=metadata,
                    codex_runtime_obj=codex_runtime_obj,
                    harness_env=harness_env,
                )
            finally:
                if global_limiter is not None:
                    global_limiter.release()

        max_workers = execution_config.testcase_jobs if execution_config is not None else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_attempt, index, tc): (index, tc)
                for index, tc in enumerate(testcases)
            }
            for future in as_completed(futures):
                index, tc = futures[future]
                try:
                    attempt = future.result()
                except Exception as e:
                    attempt = {
                        "index": index,
                        "result": None,
                        "record": {
                            "testcase_id": tc.id,
                            "category": tc.category,
                            "score": 0.0,
                            "status": "error",
                            "error": str(e),
                            **metadata,
                        },
                        "error": e,
                    }
                ordered_attempts[index] = attempt

        for index, tc in enumerate(testcases):
            attempt = ordered_attempts[index]
            assert attempt is not None
            result = attempt["result"]
            record = attempt["record"]
            records.append(record)
            with _OUTPUT_LOCK:
                if result is None:
                    click.echo(f"{tc.id}\t{tc.category}\terror\t0.00\t{record.get('error')}")
                    continue
                total += result.score
                click.echo(f"{tc.id}\t{tc.category}\t{result.verifier_result.status}\t{result.score:.2f}")
                _echo_verifier_result(result.verifier_result)
                _echo_diff(result)
                if record.get("exit_code") is not None:
                    _echo_harness_failure(tc.id, record)
        mean = total / len(testcases) if testcases else 0.0
        if testcases:
            with _OUTPUT_LOCK:
                click.echo(f"--- mean score: {mean:.3f} over {len(testcases)} testcase(s)")

        report = {
            **metadata,
            "harness": harness_id,
            "count": len(testcases),
            "mean_score": mean,
            "testcases": records,
        }
        if codex_runtime is not None:
            report["codex_harness_runtime"] = codex_runtime
        _write_report_outputs(report, report_file, cell_dir)
        return report

    for tc in testcases:
        if isinstance(drv, CommandHarnessDriver):
            drv.label = tc.id
        effective_drv = drv
        if driver_name == "patch" and cell is not None and patch_file is None:
            effective_drv = PatchDriver((tc.dir / "fix.diff").read_text(encoding="utf-8"))
        configured_timeout = None
        original_timeout = None
        if (
            cell is not None
            and isinstance(effective_drv, CommandHarnessDriver)
            and tc.id in (cell.testcase_timeouts or {})
        ):
            configured_timeout = (cell.testcase_timeouts or {})[tc.id]
            original_timeout = effective_drv.timeout
            effective_drv.timeout = configured_timeout
        try:
            result = run_testcase(repo_root, tc, effective_drv, harness_id, config,
                                  workspace_root=workspace_root,
                                  artifacts_dir=artifacts_dir)
        except Exception as e:
            click.echo(f"{tc.id}\t{tc.category}\terror\t0.00\t{e}")
            record = {"testcase_id": tc.id, "category": tc.category,
                      "score": 0.0, "status": "error", "error": str(e),
                      **metadata}
            if configured_timeout is not None:
                record["configured_timeout"] = configured_timeout
            records.append(record)
            if original_timeout is not None and isinstance(effective_drv, CommandHarnessDriver):
                effective_drv.timeout = original_timeout
            continue
        finally:
            if original_timeout is not None and isinstance(effective_drv, CommandHarnessDriver):
                effective_drv.timeout = original_timeout
        total += result.score
        click.echo(f"{tc.id}\t{tc.category}\t{result.verifier_result.status}\t{result.score:.2f}")
        _echo_verifier_result(result.verifier_result)
        _echo_diff(result)
        if isinstance(effective_drv, CommandHarnessDriver) and effective_drv.last_outcome is not None:
            _echo_harness_failure(tc.id, effective_drv.last_outcome)
        record = {**result.to_dict(), **metadata}
        if configured_timeout is not None:
            record["configured_timeout"] = configured_timeout
        if isinstance(effective_drv, CommandHarnessDriver) and effective_drv.last_outcome is not None:
            record.update(effective_drv.last_outcome)
        records.append(record)

    mean = total / len(testcases) if testcases else 0.0
    if testcases:
        click.echo(f"--- mean score: {mean:.3f} over {len(testcases)} testcase(s)")

    report = {
        **metadata,
        "harness": harness_id,
        "count": len(testcases),
        "mean_score": mean,
        "testcases": records,
    }
    if codex_runtime is not None:
        report["codex_harness_runtime"] = codex_runtime
    _write_report_outputs(report, report_file, cell_dir)
    return report


def _recommended_timeout(record: dict[str, Any]) -> float | None:
    current = record.get("configured_timeout")
    wall_time = record.get("wall_time")
    if not isinstance(current, (int, float)) or current <= 0:
        return None
    if record.get("timed_out") or record.get("stalled"):
        return float(current) * 2.0
    if not isinstance(wall_time, (int, float)) or wall_time <= 0:
        return None
    return max(float(current), float(math.ceil(wall_time * 1.5)))


def _normalize_timeout_value(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(float(value), 3)


def _update_test_set_timeouts(experiment_path: str, reports: list[dict]) -> None:
    if yaml is None:
        return
    experiment_path_obj = Path(experiment_path)
    experiment_data = yaml.safe_load(experiment_path_obj.read_text(encoding="utf-8")) or {}
    test_set_file = experiment_data.get("test_set_file")
    if not test_set_file:
        return
    test_set_path = Path(test_set_file)
    if not test_set_path.is_absolute():
        test_set_path = experiment_path_obj.parent / test_set_path
    data = yaml.safe_load(test_set_path.read_text(encoding="utf-8")) or {}
    test_sets = data.get("test_sets", {}) or {}
    changed = False
    for report in reports:
        task_set_name = report.get("task_set")
        task_set = test_sets.get(task_set_name)
        if not isinstance(task_set, dict):
            continue
        raw_testcases = task_set.get("testcases")
        if not isinstance(raw_testcases, list):
            continue
        entries_by_id: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(raw_testcases):
            if isinstance(item, str):
                entry = {"id": item}
                raw_testcases[index] = entry
                entries_by_id[item] = entry
            elif isinstance(item, dict) and item.get("id") is not None:
                entries_by_id[str(item["id"])] = item
        for record in report.get("testcases", []):
            testcase_id = record.get("testcase_id")
            if testcase_id not in entries_by_id:
                continue
            next_timeout = _recommended_timeout(record)
            if next_timeout is None:
                continue
            normalized = _normalize_timeout_value(next_timeout)
            if entries_by_id[testcase_id].get("timeout") != normalized:
                entries_by_id[testcase_id]["timeout"] = normalized
                changed = True
    if changed:
        test_set_path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


def _dry_run_experiment(experiment_path: str) -> None:
    experiment = load_experiment_file(experiment_path)
    click.echo(f"DRY RUN EXPERIMENT {experiment.experiment_id}")
    for cell in experiment.cells:
        click.echo(f"cell: {cell.cell_id}")
        click.echo(f"  model: {cell.model}")
        click.echo(f"  agent_cli: {cell.agent_cli}")
        click.echo(f"  orchestration: {cell.orchestration.id}")
        click.echo(f"  task_set: {cell.task_set}")
        click.echo(f"  testcases_dir: {cell.testcases_dir}")
        click.echo(f"  testcases: {cell.testcase_ids}")
        click.echo(f"  harness_preset: {cell.harness.preset}")
        click.echo(f"  harness_fingerprint: {cell.harness.fingerprint}")


def _run_experiment(
    *,
    experiment_path: str,
    driver: str,
    patch_file: str | None,
    harness_cmd: str | None,
    timeout: float,
    stall_timeout: float,
    log_dir: str,
    results_dir: str,
    workspace_root: str | None,
    artifacts_dir: str | None,
    stream: bool,
    godot_binary: str,
    execution_config: ExecutionConfig | None = None,
) -> list[dict]:
    experiment = load_experiment_file(experiment_path)
    execution_config = execution_config or load_execution_config()
    reports: list[dict | None] = [None] * len(experiment.cells)
    global_limiter = threading.Semaphore(execution_config.max_jobs)

    def run_cell(index: int, cell: ExperimentCell) -> dict:
        cell_dir = Path(results_dir) / cell.experiment_id / cell.cell_id
        report_path = cell_dir / "report.json"
        harness_id = cell.harness.preset or cell.agent_cli
        run_metadata = _new_run_metadata()
        codex_runtime = materialize_codex_harness(cell)
        return _run_selected_testcases(
            testcases_dir=cell.testcases_dir,
            testcase_id=None,
            testcase_ids=cell.testcase_ids,
            harness_id=harness_id,
            driver_name=driver,
            patch_file=patch_file,
            harness_cmd=harness_cmd,
            timeout=timeout,
            stall_timeout=stall_timeout,
            log_dir=str(cell_dir / "logs" if log_dir == "harness-logs" else Path(log_dir)),
            report_file=str(report_path),
            workspace_root=workspace_root,
            artifacts_dir=str(cell_dir / "artifacts") if artifacts_dir is None else artifacts_dir,
            stream=stream,
            godot_binary=godot_binary,
            cell=cell,
            cell_dir=cell_dir,
            run_metadata=run_metadata,
            codex_runtime=codex_runtime.manifest if codex_runtime else None,
            codex_runtime_obj=codex_runtime,
            harness_env=codex_runtime.env if codex_runtime else None,
            execution_config=execution_config,
            global_limiter=global_limiter,
        )

    with ThreadPoolExecutor(max_workers=execution_config.experiment_jobs) as executor:
        futures = {
            executor.submit(run_cell, index, cell): index
            for index, cell in enumerate(experiment.cells)
        }
        for future in as_completed(futures):
            index = futures[future]
            reports[index] = future.result()
    completed_reports = [report for report in reports if report is not None]
    _update_test_set_timeouts(experiment_path, completed_reports)
    return completed_reports


@main.command("run")
@click.option("--testcases-dir", default="./testcases", type=click.Path(exists=True),
              help="Directory of testcases (default: ./testcases)")
@click.option("--testcase", "testcase_id", default=None, help="Run only this testcase id")
@click.option("--harness", "harness_id", default="manual", help="Harness id label")
@click.option("--driver", type=click.Choice(["noop", "patch", "command"]), default="noop")
@click.option("--patch", "patch_file", default=None, type=click.Path(exists=True))
@click.option("--harness-cmd", "harness_cmd", default=None,
              help="Command template for --driver command, e.g. 'claude -p {task}'")
@click.option("--timeout", default=DEFAULT_TIMEOUT, type=float, help="Per-testcase harness timeout (s)")
@click.option("--stall-timeout", "stall_timeout", default=0.0, type=float,
              help="Abort a harness that produces no output for this many seconds. "
                   "Default 0 (disabled): harnesses like 'claude -p' print nothing "
                   "until done, so a stall guard would kill long healthy tasks. "
                   "Only set this for harnesses that stream progress incrementally.")
@click.option("--log-dir", "log_dir", default="harness-logs", type=click.Path(),
              help="Where to write harness stdout/stderr logs")
@click.option("--report", "report_file", default=None, type=click.Path(),
              help="Write a JSON report to this path")
@click.option("--workspace-root", "workspace_root", default=None, type=click.Path(),
              help="Where to create per-testcase workspaces (default: OS temp dir). "
                   "Use a path your harness trusts if it gates edits under temp.")
@click.option("--artifacts-dir", "artifacts_dir", default=None, type=click.Path(),
              help="Persist each testcase's harness output here (a changes.diff and a "
                   "files/ copy of every changed file) for later re-verification. The "
                   "throwaway workspace is deleted after the run, so without this the "
                   "agent's code is lost.")
@click.option("--stream/--no-stream", "stream", default=True,
              help="Stream harness output live to the screen (default: on).")
@click.option("--godot-binary", default="godot", help="Godot executable for L0/runtime verifiers")
@click.option("--experiment", "experiment_file", default=None, type=click.Path(exists=True),
              help="Run a factorial experiment YAML instead of a single testcase set")
@click.option("--results-dir", default="results", type=click.Path(),
              help="Root for experiment cell reports/manifests")
@click.option("--dry-run", is_flag=True,
              help="Print selected experiment cells without running testcases")
def run_cmd(testcases_dir: str, testcase_id: str | None, harness_id: str,
            driver: str, patch_file: str | None, harness_cmd: str | None,
            timeout: float, stall_timeout: float, log_dir: str, report_file: str | None,
            workspace_root: str | None, artifacts_dir: str | None,
            stream: bool, godot_binary: str, experiment_file: str | None,
            results_dir: str, dry_run: bool):
    """Run testcases against a harness driver (executed inside the target game repo)."""
    if experiment_file:
        if dry_run:
            _dry_run_experiment(experiment_file)
            return
        _run_experiment(
            experiment_path=experiment_file,
            driver=driver,
            patch_file=patch_file,
            harness_cmd=harness_cmd,
            timeout=timeout,
            stall_timeout=stall_timeout,
            log_dir=log_dir,
            results_dir=results_dir,
            workspace_root=workspace_root,
            artifacts_dir=artifacts_dir,
            stream=stream,
            godot_binary=godot_binary,
            execution_config=load_execution_config(),
        )
        return

    if driver == "patch" and not patch_file:
        click.echo("--patch FILE required with --driver patch")
        return
    if driver == "command" and not harness_cmd:
        click.echo("--harness-cmd TEMPLATE required with --driver command")
        return

    _run_selected_testcases(
        testcases_dir=testcases_dir,
        testcase_id=testcase_id,
        testcase_ids=None,
        harness_id=harness_id,
        driver_name=driver,
        patch_file=patch_file,
        harness_cmd=harness_cmd,
        timeout=timeout,
        stall_timeout=stall_timeout,
        log_dir=log_dir,
        report_file=report_file,
        workspace_root=workspace_root,
        artifacts_dir=artifacts_dir,
        stream=stream,
        godot_binary=godot_binary,
    )


@main.command("serve")
@click.option("--reports-dir", default=".", type=click.Path(exists=True),
              help="Directory to scan for *.json benchmark reports (default: cwd)")
@click.option("--testcases-dir", "testcases_dir", default=None, type=click.Path(),
              help="Directory of testcases to show in the Testcases tab "
                   "(default: ./testcases if it exists)")
@click.option("--port", default=8000, type=int)
@click.option("--host", default="127.0.0.1")
@click.option("--open-browser/--no-open-browser", default=True,
              help="Open the dashboard in a browser on startup (default: on)")
@click.option("--editable/--no-editable", default=False,
              help="Enable in-dashboard testcase create/edit/delete (writes to "
                   "--testcases-dir). Off by default: the dashboard is read-only "
                   "unless this flag is passed.")
def serve_cmd(reports_dir: str, testcases_dir: str | None, port: int, host: str,
              open_browser: bool, editable: bool):
    """Serve a local web dashboard to view and compare benchmark reports.

    Scans --reports-dir for report JSON files on every request, so re-running a
    benchmark and refreshing the page shows the new run immediately. Pure
    stdlib, fully offline; charts are drawn with native SVG/CSS.
    """
    import json
    import webbrowser
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs

    from aigamedevbench.webreport import (
        INDEX_HTML, load_reports, build_summary, report_detail,
        load_testcase_catalog, load_testcase_detail,
        create_testcase, save_testcase_file, delete_testcase_file,
        editor_enums, EditError,
    )

    root = Path(reports_dir)
    if testcases_dir:
        tc_root = Path(testcases_dir)
    else:
        default_tc = root / "testcases"
        tc_root = default_tc if default_tc.is_dir() else None

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                self._send(200, INDEX_HTML.encode("utf-8"),
                           "text/html; charset=utf-8")
                return
            if path == "/api/config":
                self._json({"editable": editable and tc_root is not None,
                            "has_testcases": tc_root is not None,
                            "enums": editor_enums()})
                return
            if path == "/api/summary":
                self._json(build_summary(load_reports(root)))
                return
            if path == "/api/testcases":
                self._json(load_testcase_catalog(tc_root) if tc_root else [])
                return
            if path == "/api/testcase":
                q = parse_qs(parsed.query)
                tc = (q.get("id") or [""])[0]
                detail = load_testcase_detail(tc_root, tc) if tc_root else None
                self._json(detail if detail is not None else {"error": "not found"},
                           code=200 if detail is not None else 404)
                return
            if path == "/api/detail":
                q = parse_qs(parsed.query)
                run = (q.get("run") or [""])[0]
                tc = (q.get("testcase") or [""])[0]
                for r in load_reports(root):
                    if r["_run_id"] == run:
                        detail = report_detail(r, tc, reports_dir=root)
                        if detail is not None:
                            self._json(detail)
                            return
                        break
                self._json({"error": "not found"}, code=404)
                return
            self._json({"error": "not found"}, code=404)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}
            return obj if isinstance(obj, dict) else {}

        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            parsed = urlparse(self.path)
            path = parsed.path
            # Every write endpoint is gated on --editable AND a known testcases dir.
            if not (editable and tc_root is not None):
                self._json({"error": "editing is disabled (run serve with --editable)"},
                           code=403)
                return
            body = self._read_json_body()
            try:
                if path == "/api/testcase/create":
                    result = create_testcase(tc_root, str(body.get("id", "")),
                                             str(body.get("category", "behavior_logic")),
                                             str(body.get("task", "")))
                    self._json(result)
                    return
                if path == "/api/testcase/save-file":
                    result = save_testcase_file(tc_root, str(body.get("id", "")),
                                                str(body.get("path", "")),
                                                str(body.get("content", "")))
                    self._json(result)
                    return
                if path == "/api/testcase/delete-file":
                    result = delete_testcase_file(tc_root, str(body.get("id", "")),
                                                  str(body.get("path", "")))
                    self._json(result)
                    return
            except EditError as e:
                self._json({"error": str(e)}, code=e.status)
                return
            self._json({"error": "not found"}, code=404)

        def log_message(self, *args) -> None:
            pass  # keep the console quiet; failures still raise

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    click.echo(f"--- serving {root.resolve()} at {url} (Ctrl-C to stop)")
    if tc_root:
        mode = "editable" if editable else "read-only"
        click.echo(f"--- testcases from {tc_root.resolve()} ({mode})")
    elif editable:
        click.echo("--- --editable ignored: no --testcases-dir")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\n--- stopped")
    finally:
        server.server_close()
