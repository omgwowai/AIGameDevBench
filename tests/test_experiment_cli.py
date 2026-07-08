import json
import sys
import textwrap
import time
from pathlib import Path

from click.testing import CliRunner

from aigamedevbench.cli import _codex_task_complete, main


def _write_py_config_case(root, case_id, base_attack, expected_attack):
    tc = root / case_id
    tc.mkdir(parents=True)
    (tc / "testcase.toml").write_text(
        f"""
[testcase]
id = "{case_id}"
category = "intent_translation"
source_kind = "folder"
task = "set attack"

[verifier]
type = "py_config"
entry = "expected.json"

[scoring]
mode = "fields"
""",
        encoding="utf-8",
    )
    (tc / "expected.json").write_text(
        json.dumps(
            {
                "fields": [
                    {
                        "name": "attack",
                        "aliases": ["attack"],
                        "files_glob": "**/*.json",
                        "expected": expected_attack,
                        "tol": 1e-6,
                        "base": base_attack,
                        "must_differ_from_base": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    baseline = tc / "baseline"
    (baseline / "data").mkdir(parents=True)
    (baseline / "data" / "char.json").write_text(
        json.dumps({"attack": base_attack}) + "\n", encoding="utf-8"
    )
    (tc / "fix.diff").write_text(
        "diff --git a/data/char.json b/data/char.json\n"
        "--- a/data/char.json\n"
        "+++ b/data/char.json\n"
        "@@ -1 +1 @@\n"
        f'-{{"attack": {base_attack}}}\n'
        f'+{{"attack": {expected_attack}}}\n',
        encoding="utf-8",
    )
    return tc


def _write_test_set_file(tmp_path, testcases_dir, testcase_ids, name="filtered_30"):
    test_set_file = tmp_path / "testsets.yaml"
    ids = ", ".join(testcase_ids)
    test_set_file.write_text(
        textwrap.dedent(
            f"""
            test_sets:
              {name}:
                testcases_dir: {testcases_dir.as_posix()}
                testcases: [{ids}]
            """
        ),
        encoding="utf-8",
    )
    return test_set_file


def _write_test_set_file_raw(tmp_path, content):
    test_set_file = tmp_path / "testsets.yaml"
    test_set_file.write_text(textwrap.dedent(content), encoding="utf-8")
    return test_set_file


def _write_harness_file(tmp_path):
    harness_file = tmp_path / "harnesses.yaml"
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              gd-baseline:
                target_agent_cli: generic
                components: {}
              bare-codex:
                target_agent_cli: codex
                components: {}
              all-codex:
                target_agent_cli: codex
                components:
                  instruction_files:
                    - codex-user-agents-md
                  plugins:
                    - agentic-game-development-superpowers
                  skills:
                    - fast-context
                    - transcript-viewer
                  mcp_servers:
                    - node_repl
                  hooks:
                    - codex-user-hooks
                  cli_config:
                    - codex-full-auto
                  context_artifacts:
                    - codex-memory-summary
            """
        ),
        encoding="utf-8",
    )
    return harness_file


def test_experiment_dry_run_lists_filtered_cells(tmp_path):
    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    _write_test_set_file(tmp_path, tcs, ["case-a"])
    _write_harness_file(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    experiment_file.write_text(
        textwrap.dedent(
            f"""
            id: filtered-ab
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: baseline
                model: gpt-5.4
                agent_cli: codex
                task_set: filtered_30
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["run", "--experiment", str(experiment_file), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "DRY RUN EXPERIMENT filtered-ab" in result.output
    assert "cell: baseline" in result.output
    assert "testcases_dir:" in result.output
    assert "testcases: ['case-a']" in result.output


def test_experiment_run_writes_cell_report_and_manifest(tmp_path):
    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    _write_py_config_case(tcs, "case-b", 30, 45)
    _write_test_set_file(tmp_path, tcs, ["case-a", "case-b"])
    _write_harness_file(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            f"""
            id: filtered-ab
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: baseline
                model: gpt-5.4
                agent_cli: codex
                orchestration:
                  id: single-agent
                source_type: builtin
                task_set: filtered_30
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "run",
            "--experiment",
            str(experiment_file),
            "--results-dir",
            str(results_dir),
            "--driver",
            "patch",
        ],
    )

    assert result.exit_code == 0, result.output
    cell_dir = results_dir / "filtered-ab" / "baseline"
    report = json.loads((cell_dir / "report.json").read_text(encoding="utf-8"))
    manifest = json.loads((cell_dir / "manifest.json").read_text(encoding="utf-8"))
    assert report["experiment_id"] == "filtered-ab"
    assert report["cell_id"] == "baseline"
    assert report["task_set"] == "filtered_30"
    assert report["count"] == 2
    assert report["mean_score"] == 1.0
    assert {tc["testcase_id"] for tc in report["testcases"]} == {"case-a", "case-b"}
    assert manifest["testcases_dir"] == str(tcs)
    assert manifest["testcase_ids"] == ["case-a", "case-b"]
    assert manifest["harness_preset"] == "gd-baseline"
    assert manifest["harness_target_agent_cli"] == "generic"
    assert manifest["harness_fingerprint"].startswith("sha256:")


def test_experiment_applies_per_test_timeout_from_test_set(tmp_path):
    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    _write_test_set_file_raw(
        tmp_path,
        f"""
        test_sets:
          filtered_30:
            testcases_dir: {tcs.as_posix()}
            testcases:
              - id: case-a
                timeout: 0.2
        """,
    )
    _write_harness_file(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: timeout-ab
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: baseline
                agent_cli: codex
                task_set: filtered_30
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )
    sleeper = "import time; time.sleep(2)"

    result = CliRunner().invoke(
        main,
        [
            "run",
            "--experiment",
            str(experiment_file),
            "--results-dir",
            str(results_dir),
            "--driver",
            "command",
            "--harness-cmd",
            f'{sys.executable} -c "{sleeper}"',
            "--timeout",
            "5",
            "--no-stream",
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(
        (results_dir / "timeout-ab" / "baseline" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    record = report["testcases"][0]
    assert record["testcase_id"] == "case-a"
    assert record["configured_timeout"] == 0.2
    assert record["timed_out"] is True
    assert record["wall_time"] < 1.5
    updated_testsets = (tmp_path / "testsets.yaml").read_text(encoding="utf-8")
    assert "timeout: 0.4" in updated_testsets


def test_experiment_unsupported_orchestration_writes_explicit_result(tmp_path):
    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    _write_test_set_file(tmp_path, tcs, ["case-a"])
    _write_harness_file(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            f"""
            id: filtered-ab
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: scripted
                model: gpt-5.4
                agent_cli: codex
                orchestration:
                  id: scripted-parallel-verify
                  source_type: scripted_workflow
                task_set: filtered_30
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "run",
            "--experiment",
            str(experiment_file),
            "--results-dir",
            str(results_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(
        (results_dir / "filtered-ab" / "scripted" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["mean_score"] == 0.0
    assert report["testcases"][0]["status"] == "unsupported"
    assert "Unsupported orchestration" in report["testcases"][0]["error"]


def test_codex_experiment_materializes_isolated_home_and_final_outputs(tmp_path, monkeypatch):
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"fake"}\n', encoding="utf-8")
    (source_home / "config.toml").write_text(
        textwrap.dedent(
            """
            model_provider = "openai"
            openai_base_url = "http://127.0.0.1:8462/v1/"
            model = "gpt-5.5"
            model_reasoning_effort = "xhigh"
            approval_policy = "never"
            sandbox_mode = "danger-full-access"

            [features]
            memories = true
            hooks = true

            [mcp_servers.fast_context]
            command = "fast-context"

            [mcp_servers.node_repl]
            command = "node-repl"
            """
        ),
        encoding="utf-8",
    )
    (source_home / "AGENTS.md").write_text("custom instructions\n", encoding="utf-8")
    (source_home / "hooks.json").write_text('{"hooks":[]}\n', encoding="utf-8")
    (source_home / "skills").mkdir()
    (source_home / "skills" / "agentic-game-development").mkdir()
    (source_home / "skills" / "fast-context").mkdir()
    (source_home / "skills" / "transcript-viewer").mkdir()
    plugin = (
        source_home
        / "plugins"
        / "cache"
        / "local"
        / "agentic-game-development-superpowers"
        / "0.1.0"
    )
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "skills" / "agentic-game-development").mkdir(parents=True)
    (plugin / "hooks").mkdir()
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        '{"name":"agentic-game-development-superpowers"}\n',
        encoding="utf-8",
    )
    (plugin / "hooks" / "hooks-codex.json").write_text('{"hooks":[]}\n', encoding="utf-8")
    (source_home / "memories").mkdir()
    (source_home / "memories" / "memory_summary.md").write_text("memory\n", encoding="utf-8")

    bench_home_root = tmp_path / "bench-homes"
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(source_home))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(bench_home_root))

    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    _write_test_set_file(tmp_path, tcs, ["case-a"])
    _write_harness_file(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            f"""
            id: codex-harness-ab
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: bare
                model: gpt-5.5
                model_reasoning_effort: low
                agent_cli: codex
                task_set: filtered_30
                harness: bare-codex
              - id: full
                model: gpt-5.5
                model_reasoning_effort: low
                agent_cli: codex
                task_set: filtered_30
                harness: all-codex
            """
        ),
        encoding="utf-8",
    )

    fake_harness = (
        "import json, os, pathlib; "
        "pathlib.Path('seen-home.json').write_text("
        "json.dumps({'CODEX_HOME': os.environ.get('CODEX_HOME')}), encoding='utf-8'); "
        "p=pathlib.Path('data/char.json'); p.write_text(json.dumps({'attack': 60})+chr(10))"
    )
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--experiment",
            str(experiment_file),
            "--results-dir",
            str(results_dir),
            "--driver",
            "command",
            "--harness-cmd",
            f'{sys.executable} -c "{fake_harness}"',
            "--no-stream",
        ],
    )

    assert result.exit_code == 0, result.output
    bare_dir = results_dir / "codex-harness-ab" / "bare"
    full_dir = results_dir / "codex-harness-ab" / "full"
    bare_manifest = json.loads((bare_dir / "manifest.json").read_text(encoding="utf-8"))
    full_manifest = json.loads((full_dir / "manifest.json").read_text(encoding="utf-8"))
    assert bare_manifest["runner_version"] == "0.1.0"
    assert bare_manifest["started_at"].endswith("Z")
    assert isinstance(bare_manifest["git_commit"], str)
    assert bare_manifest["codex_harness_runtime"]["codex_home"]
    assert full_manifest["codex_harness_runtime"]["codex_home"]
    assert full_manifest["harness_preset"] == "all-codex"
    assert full_manifest["harness_target_agent_cli"] == "codex"
    assert full_manifest["harness_plugins"] == ["agentic-game-development-superpowers"]
    assert full_manifest["harness_skills"] == ["fast-context", "transcript-viewer"]
    assert full_manifest["harness_mcp_servers"] == ["node_repl"]
    assert bare_manifest["codex_harness_runtime"]["codex_home"] != full_manifest["codex_harness_runtime"]["codex_home"]
    assert bare_manifest["codex_harness_runtime"]["probe"]["visibility_flags"]["has_user_agents_md"] is False
    assert full_manifest["codex_harness_runtime"]["probe"]["visibility_flags"]["has_user_agents_md"] is True
    assert full_manifest["codex_harness_runtime"]["probe"]["visibility_flags"]["has_user_skills"] is True
    assert full_manifest["codex_harness_runtime"]["probe"]["visibility_flags"]["has_memory_context"] is True
    assert full_manifest["codex_harness_runtime"]["probe"]["visibility_flags"]["has_mcp_servers"] is True
    assert (full_dir / "final_results.json").exists()
    assert (full_dir / "final_results.csv").exists()
    final = json.loads((full_dir / "final_results.json").read_text(encoding="utf-8"))
    assert final["mean_score"] == 1.0
    csv_text = (full_dir / "final_results.csv").read_text(encoding="utf-8")
    assert "experiment_id,cell_id,model,agent_cli" in csv_text
    assert "codex-harness-ab,full,gpt-5.5,codex" in csv_text
    full_record = final["testcases"][0]
    seen = json.loads(
        (Path(full_record["workspace_path"]) / "seen-home.json").read_text(
            encoding="utf-8"
        )
    )
    assert seen["CODEX_HOME"] == full_record["codex_home"]
    assert full_record["codex_home"] != full_manifest["codex_harness_runtime"]["codex_home"]


def test_parallel_attempts_write_isolated_attempt_outputs(tmp_path, monkeypatch):
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"fake"}\n', encoding="utf-8")
    (source_home / "config.toml").write_text(
        textwrap.dedent(
            """
            model_provider = "openai"
            openai_base_url = "http://127.0.0.1:8462/v1/"
            model = "gpt-5.5"
            approval_policy = "never"
            sandbox_mode = "danger-full-access"
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(source_home))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "bench-homes"))

    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    _write_py_config_case(tcs, "case-b", 30, 45)
    _write_test_set_file(tmp_path, tcs, ["case-a", "case-b"])
    _write_harness_file(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: isolated-attempts
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: bare
                model: gpt-5.5
                agent_cli: codex
                task_set: filtered_30
                harness: bare-codex
            """
        ),
        encoding="utf-8",
    )
    fake_harness = (
        "import json, os, pathlib; "
        "pathlib.Path('seen.json').write_text("
        "json.dumps({'cwd': os.getcwd(), 'CODEX_HOME': os.environ.get('CODEX_HOME'), "
        "'task': pathlib.Path('TASK.md').read_text(encoding='utf-8')}), encoding='utf-8'); "
        "p=pathlib.Path('data/char.json'); "
        "old=json.loads(p.read_text(encoding='utf-8')); "
        "old['attack'] = 60 if old['attack'] == 50 else 45; "
        "p.write_text(json.dumps(old)+chr(10), encoding='utf-8')"
    )

    result = CliRunner().invoke(
        main,
        [
            "run",
            "--experiment",
            str(experiment_file),
            "--results-dir",
            str(results_dir),
            "--driver",
            "command",
            "--harness-cmd",
            f'{sys.executable} -c "{fake_harness}"',
            "--no-stream",
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(
        (results_dir / "isolated-attempts" / "bare" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    records = sorted(report["testcases"], key=lambda item: item["testcase_id"])
    assert [record["testcase_id"] for record in records] == ["case-a", "case-b"]
    codex_homes = set()
    for record in records:
        attempt_dir = Path(record["attempt_dir"])
        workspace = Path(record["workspace_path"])
        codex_home = Path(record["codex_home"])
        codex_homes.add(record["codex_home"])
        assert workspace == attempt_dir / "workspace"
        assert (workspace / "TASK.md").exists()
        assert (workspace / "seen.json").exists()
        assert Path(record["log_path"]) == attempt_dir / "logs" / "harness.log"
        assert Path(record["events_path"]) == attempt_dir / "logs" / "events.jsonl"
        assert (attempt_dir / "result.json").exists()
        seen = json.loads((workspace / "seen.json").read_text(encoding="utf-8"))
        assert seen["cwd"] == str(workspace)
        assert seen["CODEX_HOME"] == str(codex_home)
        assert codex_home == attempt_dir / "codex_home"
        events = Path(record["events_path"]).read_text(encoding="utf-8")
        assert '"state": "running"' in events
        assert '"state": "verifying"' in events
        assert '"state": "finished"' in events
        event_lines = [
            json.loads(line)
            for line in events.splitlines()
            if line.strip()
        ]
        assert [event["state"] for event in event_lines if event["type"] == "state"] == [
            "running",
            "verifying",
            "finished",
        ]
    assert len(codex_homes) == 2


def test_codex_attempt_backs_up_transcripts_to_experiment_folder(tmp_path, monkeypatch):
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"fake"}\n', encoding="utf-8")
    (source_home / "config.toml").write_text(
        textwrap.dedent(
            """
            model_provider = "openai"
            openai_base_url = "http://127.0.0.1:8462/v1/"
            model = "gpt-5.5"
            approval_policy = "never"
            sandbox_mode = "danger-full-access"
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(source_home))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "bench-homes"))

    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    _write_test_set_file(tmp_path, tcs, ["case-a"])
    _write_harness_file(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: codex-transcript-backup
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: bare-low
                model: gpt-5.5
                agent_cli: codex
                task_set: filtered_30
                harness: bare-codex
            """
        ),
        encoding="utf-8",
    )
    fake_harness = (
        "import json, os, pathlib; "
        "home=pathlib.Path(os.environ['CODEX_HOME']); "
        "s=home/'sessions'/'2026'/'07'/'07'; s.mkdir(parents=True, exist_ok=True); "
        "payload={'timestamp':'2026-07-07T00:00:00Z','type':'session_meta','payload':{'id':'019f278b-181d-7543-b7cf-081bc3259a4b'}}; "
        "(s/'rollout.jsonl').write_text(json.dumps(payload)+chr(10), encoding='utf-8'); "
        "p=pathlib.Path('data/char.json'); p.write_text(json.dumps({'attack':60})+chr(10), encoding='utf-8')"
    )

    result = CliRunner().invoke(
        main,
        [
            "run",
            "--experiment",
            str(experiment_file),
            "--results-dir",
            str(results_dir),
            "--driver",
            "command",
            "--harness-cmd",
            f'{sys.executable} -c "{fake_harness}"',
            "--no-stream",
        ],
    )

    assert result.exit_code == 0, result.output
    backup_dir = results_dir / "codex-transcript-backup" / "transcripts"
    copied = list(backup_dir.glob("*.jsonl"))
    assert len(copied) == 1
    assert copied[0].name.startswith("bare-low__case-a__")
    assert "019f278b-181d-7543-b7cf-081bc3259a4b" in copied[0].read_text(encoding="utf-8")
    report = json.loads(
        (results_dir / "codex-transcript-backup" / "bare-low" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    record = report["testcases"][0]
    assert record["transcript_backup_dir"] == str(backup_dir.resolve())
    assert record["transcript_backup_paths"] == [str(copied[0].resolve())]


def test_codex_task_complete_detects_session_marker(tmp_path):
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "07" / "03"
    session_dir.mkdir(parents=True)
    assert _codex_task_complete(codex_home) is False
    (session_dir / "rollout.jsonl").write_text(
        '{"timestamp":"2026-07-03T00:00:00Z","type":"event_msg","payload":{"type":"task_complete"}}\n',
        encoding="utf-8",
    )

    assert _codex_task_complete(codex_home) is True


def test_codex_attempt_stops_after_task_complete_marker(tmp_path, monkeypatch):
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"fake"}\n', encoding="utf-8")
    (source_home / "config.toml").write_text(
        textwrap.dedent(
            """
            model_provider = "openai"
            model = "gpt-5.5"
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(source_home))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "bench-homes"))

    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    _write_test_set_file(tmp_path, tcs, ["case-a"])
    _write_harness_file(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: codex-complete-marker
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: bare
                model: gpt-5.5
                agent_cli: codex
                task_set: filtered_30
                harness: bare-codex
            """
        ),
        encoding="utf-8",
    )
    fake_harness = (
        "import json, os, pathlib, time; "
        "home=pathlib.Path(os.environ['CODEX_HOME']); "
        "s=home/'sessions'/'2026'/'07'/'03'; s.mkdir(parents=True, exist_ok=True); "
        "(s/'rollout.jsonl').write_text("
        "json.dumps({'timestamp':'2026-07-03T00:00:00Z','type':'event_msg','payload':{'type':'task_complete'}})+chr(10), "
        "encoding='utf-8'); "
        "p=pathlib.Path('data/char.json'); p.write_text(json.dumps({'attack':60})+chr(10), encoding='utf-8'); "
        "time.sleep(30)"
    )

    start = time.perf_counter()
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--experiment",
            str(experiment_file),
            "--results-dir",
            str(results_dir),
            "--driver",
            "command",
            "--harness-cmd",
            f'{sys.executable} -c "{fake_harness}"',
            "--timeout",
            "60",
            "--no-stream",
        ],
    )
    elapsed = time.perf_counter() - start

    assert result.exit_code == 0, result.output
    report = json.loads(
        (results_dir / "codex-complete-marker" / "bare" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    record = report["testcases"][0]
    assert elapsed < 25
    assert record["score"] == 1.0
    assert record["completed_but_hung"] is True
    assert record["timed_out"] is False


def test_experiment_uses_execution_config_for_dual_level_parallelism(tmp_path):
    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    _write_py_config_case(tcs, "case-b", 30, 45)
    _write_test_set_file(tmp_path, tcs, ["case-a", "case-b"])
    _write_harness_file(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    counter_file = tmp_path / "counter.json"
    counter_file.write_text(
        json.dumps({"current": 0, "max": 0, "events": []}), encoding="utf-8"
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: dual-parallel
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: cell-a
                model: smoke-model
                agent_cli: patch
                task_set: filtered_30
                harness: gd-baseline
              - id: cell-b
                model: smoke-model
                agent_cli: patch
                task_set: filtered_30
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )
    fake_harness = (
        "import json, pathlib, time; "
        f"counter=pathlib.Path(r'{counter_file}'); lock=pathlib.Path(str(counter)+'.lock'); "
        "\nwhile True:\n"
        "    try:\n"
        "        lock.mkdir()\n"
        "        break\n"
        "    except FileExistsError:\n"
        "        time.sleep(0.01)\n"
        "data=json.loads(counter.read_text(encoding='utf-8')); "
        "data['current'] += 1; data['max']=max(data['max'], data['current']); "
        "data['events'].append(data['current']); counter.write_text(json.dumps(data), encoding='utf-8'); "
        "lock.rmdir(); "
        "time.sleep(0.6); "
        "p=pathlib.Path('data/char.json'); old=json.loads(p.read_text(encoding='utf-8')); "
        "old['attack'] = 60 if old['attack'] == 50 else 45; "
        "p.write_text(json.dumps(old)+chr(10), encoding='utf-8'); "
        "\nwhile True:\n"
        "    try:\n"
        "        lock.mkdir()\n"
        "        break\n"
        "    except FileExistsError:\n"
        "        time.sleep(0.01)\n"
        "data=json.loads(counter.read_text(encoding='utf-8')); "
        "data['current'] -= 1; counter.write_text(json.dumps(data), encoding='utf-8'); "
        "lock.rmdir()"
    )

    result = CliRunner().invoke(
        main,
        [
            "run",
            "--experiment",
            str(experiment_file),
            "--results-dir",
            str(results_dir),
            "--driver",
            "command",
            "--harness-cmd",
            f'{sys.executable} -c "{fake_harness}"',
            "--no-stream",
        ],
    )

    assert result.exit_code == 0, result.output
    counter = json.loads(counter_file.read_text(encoding="utf-8"))
    assert counter["max"] >= 2
    assert counter["max"] <= 4
    for cell_id in ("cell-a", "cell-b"):
        report = json.loads(
            (results_dir / "dual-parallel" / cell_id / "report.json").read_text(
                encoding="utf-8"
            )
        )
        assert [record["testcase_id"] for record in report["testcases"]] == [
            "case-a",
            "case-b",
        ]
        assert report["mean_score"] == 1.0
