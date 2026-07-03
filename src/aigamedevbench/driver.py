from __future__ import annotations

import queue
import re
import shlex
import subprocess
import threading
import time
import os
from pathlib import Path
from typing import Callable, Protocol

from aigamedevbench.workspace import apply_patch


class HarnessDriver(Protocol):
    def run(self, task: str, workspace: Path) -> None: ...


class NoOpDriver:
    def run(self, task: str, workspace: Path) -> None:
        return None


class PatchDriver:
    def __init__(self, patch_text: str):
        self.patch_text = patch_text

    def run(self, task: str, workspace: Path) -> None:
        apply_patch(workspace, self.patch_text)


class CommandHarnessDriver:
    """Runs an arbitrary CLI harness inside the workspace to complete the task.

    The command template is shlex-split (template paths should use forward slashes
    on Windows), then each token has placeholders substituted: {task} (raw task
    text as a single arg), {task_file} (path to workspace/TASK.md), {workspace}
    (workspace path). No shell is used, so the task text can contain any
    characters without injection risk.

    Output is streamed live (line by line) to the log and to an optional on_line
    callback, so a harness that blocks (e.g. on its own permission prompt) is
    visible immediately instead of only after the overall timeout. The harness's
    stdin is closed: an interactive prompt then gets EOF and should fail fast
    rather than hang.

    Guards against a hang: the overall `timeout`, an approval-prompt marker set
    that aborts early with a clear hint, and an OPTIONAL `stall_timeout`
    (no output for that long -> abort). stall_timeout defaults to 0 (disabled)
    because many harnesses (e.g. `claude -p` in plain text mode) emit nothing
    until they finish — for those, "no output" means "still working", not
    "hung", so a stall guard would kill long-but-healthy tasks. Only enable it
    for harnesses that stream progress incrementally.
    """

    # Substrings (matched case-insensitively) that mean the harness is blocked
    # waiting for an interactive approval that will never come in batch mode.
    APPROVAL_MARKERS = (
        "waiting on your permission",
        "queued and waiting",
        "permission approval",
        "waiting for approval",
        "approve this edit",
        "permission to ",
    )

    def __init__(self, cmd_template: str, timeout: float = 600.0,
                 log_dir: Path | None = None, stall_timeout: float = 0.0,
                 on_line: Callable[[str], None] | None = None,
                 env: dict[str, str] | None = None):
        self.cmd_template = cmd_template
        self.timeout = timeout
        self.stall_timeout = stall_timeout
        self.on_line = on_line
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.env = {str(k): str(v) for k, v in (env or {}).items()}
        self.label = ""
        self.last_outcome: dict | None = None
        self._counter = 0

    def _build_argv(self, task: str, task_file: Path, workspace: Path) -> list[str]:
        repl = {
            "{task}": task,
            "{task_file}": str(task_file),
            "{workspace}": str(workspace),
        }
        # Single-pass substitution: only placeholders present in the ORIGINAL
        # template token are replaced, so substituted values that happen to
        # contain a placeholder literal (e.g. a task mentioning "{workspace}")
        # are not re-scanned and mangled.
        pattern = re.compile("|".join(re.escape(k) for k in repl))
        # shlex.split() runs in POSIX mode and would treat backslashes as escapes,
        # mangling Windows paths in the template (e.g. sys.executable). Normalize the
        # template (NOT the substituted values) to forward slashes; placeholders are
        # filled in after the split, so task content and injection-safety are unaffected.
        # Limitation: every backslash in the template is treated as a path separator, so
        # backslashes that are NOT path separators (UNC paths like \\server\share, or
        # regex escapes) will be altered — pass those via a wrapper script or use forward slashes.
        return [
            pattern.sub(lambda m: repl[m.group(0)], token)
            for token in shlex.split(self.cmd_template.replace("\\", "/"))
        ]

    def _log_path(self, workspace: Path) -> Path | None:
        if self.log_dir is None:
            return None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in (self.label or "run"))
        self._counter += 1
        return self.log_dir / f"{self._counter:04d}-{safe}.log"

    def _is_approval_block(self, line: str) -> bool:
        low = line.lower()
        return any(m in low for m in self.APPROVAL_MARKERS)

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            pass

    def _emit(self, line: str, sink: list[str]) -> None:
        sink.append(line)
        if self.on_line is not None:
            try:
                self.on_line(line)
            except Exception:
                pass

    def run(self, task: str, workspace: Path) -> None:
        workspace = Path(workspace)
        start = time.perf_counter()
        exit_code = 0
        timed_out = False
        stalled = False
        blocked_on_approval = False
        lines: list[str] = []
        argv: list[str] = []
        log_path = None
        proc: subprocess.Popen | None = None
        try:
            # Inside the try so a malformed template (shlex.split ValueError),
            # an unwritable workspace, or a log-dir mkdir failure is recorded as
            # a failed outcome rather than propagating and aborting the batch.
            task_file = workspace / "TASK.md"
            task_file.write_text(task, encoding="utf-8")
            argv = self._build_argv(task, task_file, workspace)
            log_path = self._log_path(workspace)
            proc = subprocess.Popen(
                argv, cwd=str(workspace),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,  # no TTY: interactive prompts get EOF
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env={**os.environ, **self.env} if self.env else None,
            )

            # A reader thread feeds lines into a queue so the main loop can apply
            # the overall timeout and the stall watchdog even when the harness
            # produces no output at all (a select() on pipes is not portable to
            # Windows, hence the thread).
            q: queue.Queue[str | None] = queue.Queue()

            def _reader() -> None:
                try:
                    assert proc is not None and proc.stdout is not None
                    for raw in proc.stdout:
                        q.put(raw)
                finally:
                    q.put(None)  # sentinel: stream closed

            reader = threading.Thread(target=_reader, daemon=True)
            reader.start()

            last_activity = time.perf_counter()
            while True:
                now = time.perf_counter()
                if now - start > self.timeout:
                    timed_out = True
                    break
                if self.stall_timeout and now - last_activity > self.stall_timeout:
                    stalled = True
                    break
                try:
                    item = q.get(timeout=0.5)
                except queue.Empty:
                    if proc.poll() is not None:
                        break  # process exited and pipe drained
                    continue
                if item is None:
                    break
                last_activity = time.perf_counter()
                self._emit(item.rstrip("\n"), lines)
                if self._is_approval_block(item):
                    blocked_on_approval = True
                    break

            if timed_out or stalled or blocked_on_approval:
                self._terminate(proc)
                exit_code = -1
                if blocked_on_approval:
                    self._emit(
                        "[aigdbench] harness is waiting for interactive approval; "
                        "run it in an autonomous mode (e.g. Claude Code: add "
                        "--dangerously-skip-permissions) so edits are not gated.",
                        lines,
                    )
                elif stalled:
                    self._emit(
                        f"[aigdbench] no output for {self.stall_timeout:.0f}s; "
                        "aborting (possible interactive prompt or hang).",
                        lines,
                    )
                elif timed_out:
                    self._emit(
                        f"[aigdbench] overall timeout {self.timeout:.0f}s exceeded; "
                        "aborting.",
                        lines,
                    )
            else:
                exit_code = proc.wait()
        except FileNotFoundError as e:
            exit_code = -1
            self._emit(f"[harness binary not found] {e}", lines)
        except Exception as e:  # never propagate to the batch loop
            exit_code = -1
            self._emit(f"[driver error] {e!r}", lines)
        finally:
            if proc is not None and proc.poll() is None:
                self._terminate(proc)
        wall_time = time.perf_counter() - start

        if log_path is not None:
            try:
                log_path.write_text(
                    f"$ {' '.join(argv)}\n"
                    f"exit_code={exit_code} timed_out={timed_out} "
                    f"stalled={stalled} blocked_on_approval={blocked_on_approval} "
                    f"wall_time={wall_time:.3f}\n"
                    f"--- output ---\n" + "\n".join(lines) + "\n",
                    encoding="utf-8",
                )
            except Exception:  # a logging failure must not abort the batch
                pass

        self.last_outcome = {
            "exit_code": exit_code,
            "wall_time": wall_time,
            "timed_out": timed_out,
            "stalled": stalled,
            "blocked_on_approval": blocked_on_approval,
            "log_path": str(log_path) if log_path is not None else None,
        }
