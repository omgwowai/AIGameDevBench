from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from aigamedevbench.driver import NoOpDriver, PatchDriver, CommandHarnessDriver


def test_noop_changes_nothing(tmp_path):
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    NoOpDriver().run("task", tmp_path)
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "a\n"


def test_patch_driver_applies(tmp_path):
    from aigamedevbench.git_ops import git_run
    git_run(["init"], cwd=tmp_path)
    git_run(["config", "user.email", "t@t.com"], cwd=tmp_path)
    git_run(["config", "user.name", "t"], cwd=tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    git_run(["add", "f.txt"], cwd=tmp_path)
    git_run(["commit", "-m", "i"], cwd=tmp_path)
    (tmp_path / "f.txt").write_text("b\n", encoding="utf-8")
    patch = git_run(["diff"], cwd=tmp_path)
    git_run(["checkout", "f.txt"], cwd=tmp_path)
    PatchDriver(patch).run("task", tmp_path)
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "b\n"


def _echo_argv_harness(out_file: Path) -> str:
    """A fake harness command template: a python one-liner that writes its
    own argv (everything after the script) to out_file, one arg per line."""
    script = (
        "import sys, pathlib; "
        f"pathlib.Path(r'{out_file}').write_text("
        "chr(10).join(sys.argv[1:]), encoding='utf-8')"
    )
    # {task} is passed as a single argv element after the script.
    return f'{sys.executable} -c "{script}" {{task}}'


def test_command_driver_writes_task_file(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    out = tmp_path / "argv.txt"
    drv = CommandHarnessDriver(_echo_argv_harness(out), timeout=30,
                               log_dir=tmp_path / "logs")
    drv.run("do the thing", ws)
    assert (ws / "TASK.md").read_text(encoding="utf-8") == "do the thing"
    assert drv.last_outcome["exit_code"] == 0
    assert out.exists()


def test_command_driver_task_is_single_arg(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    out = tmp_path / "argv.txt"
    drv = CommandHarnessDriver(_echo_argv_harness(out), timeout=30,
                               log_dir=tmp_path / "logs")
    drv.run("multi word task", ws)
    # argv after the script is exactly ONE line: the whole task string.
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines == ["multi word task"]


def test_command_driver_records_outcome(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    out = tmp_path / "argv.txt"
    drv = CommandHarnessDriver(_echo_argv_harness(out), timeout=30,
                               log_dir=tmp_path / "logs")
    drv.label = "tc-1"
    drv.run("task", ws)
    o = drv.last_outcome
    assert set(o) == {
        "exit_code",
        "wall_time",
        "timed_out",
        "stalled",
        "blocked_on_approval",
        "completed_but_hung",
        "log_path",
        "ai_agent_context",
    }
    assert o["exit_code"] == 0
    assert o["timed_out"] is False
    assert o["wall_time"] >= 0.0
    assert Path(o["log_path"]).exists()
    # The event parser always populates a context block (turns may be empty for
    # a harness that emits no recognisable events).
    assert set(o["ai_agent_context"]) == {
        "turns",
        "total_tokens",
        "slowest_turn",
        "total_turn_ms",
    }


def test_command_driver_nonzero_exit_does_not_raise(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    cmd = f'{sys.executable} -c "import sys; sys.exit(3)"'
    drv = CommandHarnessDriver(cmd, timeout=30, log_dir=tmp_path / "logs")
    drv.run("task", ws)  # must not raise
    assert drv.last_outcome["exit_code"] == 3
    assert drv.last_outcome["timed_out"] is False


def test_command_driver_timeout_is_killed(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    cmd = f'{sys.executable} -c "import time; time.sleep(30)"'
    drv = CommandHarnessDriver(cmd, timeout=1, log_dir=tmp_path / "logs")
    drv.run("task", ws)  # must not raise, returns quickly after kill
    assert drv.last_outcome["timed_out"] is True
    assert drv.last_outcome["exit_code"] == -1


def _pid_is_running(pid: int) -> bool:
    if sys.platform == "win32":
        import subprocess
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return f'"{pid}"' in proc.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_command_driver_timeout_kills_child_process_tree(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    child_pid_file = tmp_path / "child.pid"
    child_script = tmp_path / "child.py"
    child_script.write_text(
        "import pathlib, sys, time\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "pid_file = pathlib.Path(sys.argv[2])\n"
        "handle = path.open('w')\n"
        "handle.write('locked')\n"
        "handle.flush()\n"
        "pid_file.write_text(str(__import__('os').getpid()), encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    locked_file = ws / "locked.txt"
    cmd = (
        f"{sys.executable} {parent_script} "
        f"{child_script} {locked_file} {child_pid_file}"
    )
    drv = CommandHarnessDriver(cmd, timeout=1, log_dir=tmp_path / "logs")

    drv.run("task", ws)

    assert drv.last_outcome["timed_out"] is True
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert not _pid_is_running(child_pid)


def test_command_driver_completion_probe_kills_hung_process(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    marker = tmp_path / "complete.marker"
    script = tmp_path / "hang_after_complete.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text('done', encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    drv = CommandHarnessDriver(
        f"{sys.executable} {script} {marker}",
        timeout=30,
        log_dir=tmp_path / "logs",
        completion_probe=lambda: marker.exists(),
        completion_grace_timeout=0.2,
    )

    start = time.perf_counter()
    drv.run("task", ws)
    elapsed = time.perf_counter() - start

    assert elapsed < 10
    assert drv.last_outcome["completed_but_hung"] is True
    assert drv.last_outcome["timed_out"] is False
    assert drv.last_outcome["exit_code"] == -1


def test_command_driver_missing_binary_does_not_raise(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    drv = CommandHarnessDriver("definitely-not-a-real-binary-xyz {task}",
                               timeout=30, log_dir=tmp_path / "logs")
    drv.run("task", ws)  # must not raise
    assert drv.last_outcome["exit_code"] == -1
    assert drv.last_outcome["timed_out"] is False


def test_command_driver_produces_detected_change(tmp_path):
    from aigamedevbench.git_ops import git_run
    ws = tmp_path / "ws"; ws.mkdir()
    git_run(["init"], cwd=ws)
    git_run(["config", "user.email", "t@t.com"], cwd=ws)
    git_run(["config", "user.name", "t"], cwd=ws)
    (ws / "f.txt").write_text("a\n", encoding="utf-8")
    git_run(["add", "f.txt"], cwd=ws)
    git_run(["commit", "-m", "i"], cwd=ws)
    script = "import pathlib; pathlib.Path('made.txt').write_text('x')"
    cmd = f'{sys.executable} -c "{script}"'
    drv = CommandHarnessDriver(cmd, timeout=30, log_dir=tmp_path / "logs")
    drv.run("task", ws)
    status = git_run(["status", "--porcelain"], cwd=ws)
    assert "made.txt" in status


def test_command_driver_malformed_template_does_not_raise(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    # Unbalanced quote -> shlex.split raises ValueError. Must be caught and
    # recorded as a failed outcome, not propagated to abort the batch.
    drv = CommandHarnessDriver('mytool "unterminated {task}',
                               timeout=30, log_dir=tmp_path / "logs")
    drv.run("task", ws)  # must not raise
    assert drv.last_outcome["exit_code"] == -1
    assert drv.last_outcome["timed_out"] is False


def test_command_driver_task_with_placeholder_literal_not_mangled(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    out = tmp_path / "argv.txt"
    drv = CommandHarnessDriver(_echo_argv_harness(out), timeout=30,
                               log_dir=tmp_path / "logs")
    # A task that literally mentions another placeholder must be delivered
    # verbatim, not have that placeholder re-substituted.
    drv.run("update the {workspace} handling", ws)
    assert out.read_text(encoding="utf-8").splitlines() == \
        ["update the {workspace} handling"]


def test_command_driver_aborts_on_approval_prompt(tmp_path):
    # A harness that prints an approval-waiting line then blocks must be aborted
    # immediately (not after the full timeout), with blocked_on_approval set.
    ws = tmp_path / "ws"; ws.mkdir()
    script = (
        "import sys, time; "
        "print('edits are queued and waiting on your permission approval'); "
        "sys.stdout.flush(); time.sleep(60)"
    )
    cmd = f'{sys.executable} -c "{script}"'
    drv = CommandHarnessDriver(cmd, timeout=30, log_dir=tmp_path / "logs",
                               stall_timeout=20)
    import time as _t
    start = _t.perf_counter()
    drv.run("task", ws)  # must not raise
    elapsed = _t.perf_counter() - start
    assert drv.last_outcome["blocked_on_approval"] is True
    assert drv.last_outcome["exit_code"] == -1
    assert elapsed < 15, "should abort well before timeout/stall"


def test_command_driver_aborts_on_stall(tmp_path):
    # A silent harness (no output) must be aborted by the stall watchdog before
    # the overall timeout.
    ws = tmp_path / "ws"; ws.mkdir()
    cmd = f'{sys.executable} -c "import time; time.sleep(30)"'
    drv = CommandHarnessDriver(cmd, timeout=60, log_dir=tmp_path / "logs",
                               stall_timeout=2)
    drv.run("task", ws)  # must not raise
    assert drv.last_outcome["stalled"] is True
    assert drv.last_outcome["exit_code"] == -1


def test_command_driver_streams_lines(tmp_path):
    # on_line is invoked live for each output line.
    ws = tmp_path / "ws"; ws.mkdir()
    cmd = f'{sys.executable} -c "print(chr(104)+chr(105))"'  # prints "hi"
    got = []
    drv = CommandHarnessDriver(cmd, timeout=30, log_dir=tmp_path / "logs",
                               on_line=got.append)
    drv.run("task", ws)
    assert "hi" in got
    assert drv.last_outcome["exit_code"] == 0


def test_command_driver_streams_to_log_before_exit(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    log_dir = tmp_path / "logs"
    script = (
        "import sys, time; "
        "print('progress one'); sys.stdout.flush(); "
        "time.sleep(2)"
    )
    drv = CommandHarnessDriver(
        f'{sys.executable} -c "{script}"',
        timeout=30,
        log_dir=log_dir,
        log_name="harness.log",
    )

    thread = threading.Thread(target=lambda: drv.run("task", ws))
    thread.start()
    deadline = time.perf_counter() + 1.5
    log_path = log_dir / "harness.log"
    saw_progress = False
    while time.perf_counter() < deadline:
        if log_path.exists() and "progress one" in log_path.read_text(encoding="utf-8"):
            saw_progress = True
            break
        time.sleep(0.05)
    thread.join(timeout=5)

    assert saw_progress
    assert drv.last_outcome["exit_code"] == 0


def test_command_driver_no_shell_injection(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    canary = ws / "canary.txt"
    canary.write_text("alive", encoding="utf-8")
    out = tmp_path / "argv.txt"
    drv = CommandHarnessDriver(_echo_argv_harness(out), timeout=30,
                               log_dir=tmp_path / "logs")
    # If the task were ever interpolated into a shell, this would delete canary.
    # Use platform-specific shell command so the test is probative on all platforms.
    if sys.platform == "win32":
        payload = f"& del {canary}"      # cmd.exe command separator + delete
    else:
        payload = f"; rm -rf {canary}"   # POSIX shell separator + delete
    drv.run(payload, ws)
    assert canary.read_text(encoding="utf-8") == "alive"
    # And the malicious string arrives intact as one argv element.
    assert out.read_text(encoding="utf-8").splitlines() == [payload]


def test_command_driver_injects_environment(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    out = tmp_path / "env.json"
    script = (
        "import json, os, pathlib; "
        f"pathlib.Path(r'{out}').write_text("
        "json.dumps({'CODEX_HOME': os.environ.get('CODEX_HOME'), "
        "'AIGDB_MARKER': os.environ.get('AIGDB_MARKER')}), "
        "encoding='utf-8')"
    )
    drv = CommandHarnessDriver(
        f'{sys.executable} -c "{script}"',
        timeout=30,
        log_dir=tmp_path / "logs",
        env={"CODEX_HOME": str(tmp_path / "codex-home"), "AIGDB_MARKER": "present"},
    )

    drv.run("task", ws)

    assert drv.last_outcome["exit_code"] == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == {
        "CODEX_HOME": str(tmp_path / "codex-home"),
        "AIGDB_MARKER": "present",
    }
