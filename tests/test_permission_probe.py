from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from click.testing import CliRunner

from aigamedevbench.cli import main
from aigamedevbench.permission_probe import run_permission_probe


def _write_probe_harness(path: Path, *, touch_outside: bool = False, leak_secret: bool = False) -> None:
    path.write_text(
        textwrap.dedent(
            f"""
            import pathlib
            import sys

            task = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
            values = {{}}
            for line in task.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()

            pathlib.Path(values["INSIDE_WRITE_PATH"]).write_text("inside-ok\\n", encoding="utf-8")

            if {leak_secret!r}:
                secret = pathlib.Path(values["OUTSIDE_READ_PATH"]).read_text(encoding="utf-8")
                pathlib.Path(values["OUTSIDE_READ_LEAK_PATH"]).write_text(secret, encoding="utf-8")

            if {touch_outside!r}:
                pathlib.Path(values["OUTSIDE_WRITE_PATH"]).write_text("outside-bad\\n", encoding="utf-8")
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_permission_probe_passes_when_outside_read_write_are_blocked(tmp_path):
    script = tmp_path / "harness.py"
    _write_probe_harness(script)

    result = run_permission_probe(
        f"{sys.executable} {script} {{task_file}}",
        tmp_path / "probe",
        timeout=10,
    )

    assert result["passed"] is True
    assert result["inside_write_ok"] is True
    assert result["outside_read_blocked"] is True
    assert result["outside_write_blocked"] is True
    assert Path(result["result_path"]).exists()


def test_permission_probe_fails_when_harness_can_touch_outside_paths(tmp_path):
    script = tmp_path / "harness.py"
    _write_probe_harness(script, touch_outside=True, leak_secret=True)

    result = run_permission_probe(
        f"{sys.executable} {script} {{task_file}}",
        tmp_path / "probe",
        timeout=10,
    )

    assert result["passed"] is False
    assert result["inside_write_ok"] is True
    assert result["outside_read_blocked"] is False
    assert result["outside_write_blocked"] is False


def test_probe_permissions_cli_writes_machine_readable_result(tmp_path):
    script = tmp_path / "harness.py"
    _write_probe_harness(script)
    probe_root = tmp_path / "probe"

    result = CliRunner().invoke(
        main,
        [
            "probe-permissions",
            "--harness-cmd",
            f"{sys.executable} {script} {{task_file}}",
            "--probe-root",
            str(probe_root),
            "--timeout",
            "10",
            "--no-stream",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    data = json.loads((probe_root / "permission_probe.json").read_text(encoding="utf-8"))
    assert data["passed"] is True
    assert data["workspace_path"] == str(probe_root / "workspace")
