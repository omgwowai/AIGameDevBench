import json
import sys
import textwrap

from click.testing import CliRunner

from aigamedevbench.cli import main


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


def test_experiment_dry_run_lists_filtered_cells(tmp_path):
    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    experiment_file = tmp_path / "experiment.yaml"
    experiment_file.write_text(
        textwrap.dedent(
            f"""
            id: filtered-ab
            task_sets:
              filtered30:
                testcases_dir: {tcs.as_posix()}
                testcases: [case-a]
            cells:
              - id: baseline
                model: gpt-5.4
                agent_cli: codex
                task_set: filtered30
                harness:
                  preset: gd-baseline
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
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            f"""
            id: filtered-ab
            task_sets:
              filtered30:
                testcases_dir: {tcs.as_posix()}
                testcases: [case-a, case-b]
            cells:
              - id: baseline
                model: gpt-5.4
                agent_cli: codex
                orchestration:
                  id: single-agent
                  source_type: builtin
                task_set: filtered30
                harness:
                  preset: gd-baseline
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
    assert report["task_set"] == "filtered30"
    assert report["count"] == 2
    assert report["mean_score"] == 1.0
    assert {tc["testcase_id"] for tc in report["testcases"]} == {"case-a", "case-b"}
    assert manifest["testcases_dir"] == str(tcs)
    assert manifest["testcase_ids"] == ["case-a", "case-b"]
    assert manifest["harness_preset"] == "gd-baseline"
    assert manifest["harness_fingerprint"].startswith("sha256:")


def test_experiment_unsupported_orchestration_writes_explicit_result(tmp_path):
    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            f"""
            id: filtered-ab
            task_sets:
              filtered30:
                testcases_dir: {tcs.as_posix()}
                testcases: [case-a]
            cells:
              - id: scripted
                model: gpt-5.4
                agent_cli: codex
                orchestration:
                  id: scripted-parallel-verify
                  source_type: scripted_workflow
                task_set: filtered30
                harness:
                  preset: gd-baseline
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
            """
        ),
        encoding="utf-8",
    )
    (source_home / "AGENTS.md").write_text("custom instructions\n", encoding="utf-8")
    (source_home / "hooks.json").write_text('{"hooks":[]}\n', encoding="utf-8")
    (source_home / "skills").mkdir()
    (source_home / "skills" / "agentic-game-development").mkdir()
    (source_home / "memories").mkdir()
    (source_home / "memories" / "memory_summary.md").write_text("memory\n", encoding="utf-8")

    bench_home_root = tmp_path / "bench-homes"
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(source_home))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(bench_home_root))

    tcs = tmp_path / "testcases_filtered"
    _write_py_config_case(tcs, "case-a", 50, 60)
    experiment_file = tmp_path / "experiment.yaml"
    results_dir = tmp_path / "results"
    experiment_file.write_text(
        textwrap.dedent(
            f"""
            id: codex-harness-ab
            task_sets:
              filtered30:
                testcases_dir: {tcs.as_posix()}
                testcases: [case-a]
            cells:
              - id: bare
                model: gpt-5.5
                model_reasoning_effort: low
                agent_cli: codex
                task_set: filtered30
                harness:
                  preset: bare-codex
              - id: full
                model: gpt-5.5
                model_reasoning_effort: low
                agent_cli: codex
                task_set: filtered30
                harness:
                  preset: custom-current-full
            """
        ),
        encoding="utf-8",
    )

    seen_home = tmp_path / "seen-home.json"
    fake_harness = (
        "import json, os, pathlib; "
        f"pathlib.Path(r'{seen_home}').write_text("
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
    seen = json.loads(seen_home.read_text(encoding="utf-8"))
    assert seen["CODEX_HOME"] == full_manifest["codex_harness_runtime"]["codex_home"]
