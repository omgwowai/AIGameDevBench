from __future__ import annotations

import json
import sys

from click.testing import CliRunner

from aigamedevbench.cli import main, _echo_verifier_result
from aigamedevbench.git_ops import git_run
from aigamedevbench.result import CheckResult, VerifierResult


def test_echo_verifier_result_shows_expected_actual(capsys):
    # A failed check must show expected vs actual on screen so the user sees
    # WHAT was wrong, not just that it failed.
    vr = VerifierResult(score=0.0, status="fail", checks=[
        CheckResult("state", False, detail="wrong state",
                    expected="CardBaseState", actual="CardClickedState"),
    ])
    _echo_verifier_result(vr)
    err = capsys.readouterr().err
    assert "[FAIL] state" in err
    assert "CardBaseState" in err and "CardClickedState" in err
    assert "expected" in err.lower() and "actual" in err.lower()


def test_echo_verifier_result_omits_expected_actual_when_absent(capsys):
    # A passing check with no expected/actual must not print empty expected/actual.
    vr = VerifierResult(score=1.0, status="pass", checks=[
        CheckResult("ok", True, detail="fine"),
    ])
    _echo_verifier_result(vr)
    err = capsys.readouterr().err
    assert "[PASS] ok" in err
    assert "expected" not in err.lower() and "actual" not in err.lower()


def _repo_and_testcases(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    git_run(["init"], cwd=repo)
    git_run(["config", "user.email", "t@t.com"], cwd=repo)
    git_run(["config", "user.name", "t"], cwd=repo)
    (repo / "data").mkdir()
    (repo / "data" / "char.json").write_text(json.dumps({"attack": 50}) + "\n", encoding="utf-8")
    git_run(["add", "."], cwd=repo)
    git_run(["commit", "-m", "baseline"], cwd=repo)
    head = git_run(["rev-parse", "HEAD"], cwd=repo).strip()

    tcs = tmp_path / "testcases"
    tc = tcs / "bench-0001"; tc.mkdir(parents=True)
    (tc / "testcase.toml").write_text(f"""
[testcase]
id = "bench-0001"
category = "intent_translation"
baseline_ref = "{head}"
task = "set attack"

[verifier]
type = "py_config"
entry = "verify.py"

[scoring]
mode = "fields"
""", encoding="utf-8")
    (tc / "expected.json").write_text(json.dumps({"fields": [
        {"name": "attack", "aliases": ["attack"], "files_glob": "**/*.json",
         "expected": 60, "tol": 1e-6, "base": 50, "must_differ_from_base": True},
    ]}), encoding="utf-8")
    return repo, tcs


def test_list(tmp_path):
    repo, tcs = _repo_and_testcases(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--testcases-dir", str(tcs)])
    assert result.exit_code == 0
    assert "bench-0001" in result.output
    assert "intent_translation" in result.output


def test_run_noop_fails(tmp_path, monkeypatch):
    repo, tcs = _repo_and_testcases(tmp_path)
    monkeypatch.chdir(repo)
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--testcases-dir", str(tcs), "--driver", "noop"])
    assert result.exit_code == 0
    assert "bench-0001" in result.output
    assert "fail" in result.output.lower()


def test_run_command_driver_writes_report(tmp_path, monkeypatch):
    repo, tcs = _repo_and_testcases(tmp_path)
    monkeypatch.chdir(repo)
    # A fake harness that sets attack to 60 by overwriting the json file.
    script = (
        "import pathlib, json; "
        "p=pathlib.Path('data/char.json'); "
        "p.write_text(json.dumps({'attack': 60})+chr(10))"
    )
    cmd = f'{sys.executable} -c "{script}"'
    report = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(main, [
        "run", "--testcases-dir", str(tcs),
        "--driver", "command",
        "--harness-cmd", cmd,
        "--harness", "fake-harness",
        "--timeout", "60",
        "--log-dir", str(tmp_path / "logs"),
        "--report", str(report),
    ])
    assert result.exit_code == 0, result.output
    assert "bench-0001" in result.output
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["harness"] == "fake-harness"
    assert data["count"] == 1
    assert data["mean_score"] == 1.0
    tc0 = data["testcases"][0]
    assert tc0["score"] == 1.0
    assert "wall_time" in tc0 and "exit_code" in tc0 and "log_path" in tc0 and "timed_out" in tc0
    assert tc0["exit_code"] == 0


def test_run_git_testcase_from_non_git_cwd_does_not_abort_batch(tmp_path, monkeypatch):
    # Regression: a git-type testcase scored from a non-git cwd used to make
    # get_repo_root exit 128 and abort the whole batch before any folder-type
    # testcase ran. Now the git-type case errors individually and the batch
    # continues.
    repo, tcs = _repo_and_testcases(tmp_path)

    # Add a self-contained folder-type testcase alongside the git-type one.
    folder_tc = tcs / "folder-0001"; folder_tc.mkdir()
    (folder_tc / "testcase.toml").write_text("""
[testcase]
id = "folder-0001"
category = "intent_translation"
source_kind = "folder"
task = "set attack"

[verifier]
type = "py_config"
entry = "expected.json"

[scoring]
mode = "fields"
""", encoding="utf-8")
    (folder_tc / "expected.json").write_text(json.dumps({"fields": [
        {"name": "attack", "aliases": ["attack"], "files_glob": "**/*.json",
         "expected": 60, "tol": 1e-6, "base": 50, "must_differ_from_base": True},
    ]}), encoding="utf-8")
    baseline = folder_tc / "baseline"; (baseline / "data").mkdir(parents=True)
    (baseline / "data" / "char.json").write_text(json.dumps({"attack": 50}) + "\n", encoding="utf-8")

    non_git = tmp_path / "elsewhere"; non_git.mkdir()
    monkeypatch.chdir(non_git)
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--testcases-dir", str(tcs), "--driver", "noop"])

    assert result.exit_code == 0, result.output
    # git-type testcase reports an error row instead of crashing the run
    assert "bench-0001" in result.output
    assert "error" in result.output.lower()
    # folder-type testcase still ran (noop -> fail, not skipped)
    assert "folder-0001" in result.output


def test_run_command_driver_echoes_failure_log(tmp_path, monkeypatch):
    # A non-zero harness exit must surface its log tail on screen, not just in a
    # file, so the user sees what broke immediately.
    repo, tcs = _repo_and_testcases(tmp_path)
    monkeypatch.chdir(repo)
    script = "import sys; print('BOOM diagnostic'); sys.exit(3)"
    cmd = f'{sys.executable} -c "{script}"'
    runner = CliRunner()
    result = runner.invoke(main, [
        "run", "--testcases-dir", str(tcs),
        "--driver", "command", "--harness-cmd", cmd,
        "--log-dir", str(tmp_path / "logs"),
    ])
    assert result.exit_code == 0, result.output
    assert "exit_code=3" in result.output
    assert "BOOM diagnostic" in result.output


def test_run_honors_workspace_root(tmp_path, monkeypatch):
    # --workspace-root must place the per-testcase workspace under the given dir
    # (so a harness that distrusts the OS temp path can run without hanging).
    repo, tcs = _repo_and_testcases(tmp_path)
    monkeypatch.chdir(repo)
    wsroot = tmp_path / "trusted_ws"
    # A fake harness that records its own cwd (== the workspace) into a file
    # under the repo, so we can assert where the workspace was created.
    marker = tmp_path / "where.txt"
    script = (
        "import os, pathlib; "
        f"pathlib.Path(r'{marker}').write_text(os.getcwd())"
    )
    cmd = f'{sys.executable} -c "{script}"'
    runner = CliRunner()
    result = runner.invoke(main, [
        "run", "--testcases-dir", str(tcs),
        "--driver", "command", "--harness-cmd", cmd,
        "--log-dir", str(tmp_path / "logs"),
        "--workspace-root", str(wsroot),
    ])
    assert result.exit_code == 0, result.output
    where = marker.read_text(encoding="utf-8")
    assert str(wsroot) in where, where



def test_audit_cli_writes_health_json(tmp_path, monkeypatch):
    repo, tcs = _repo_and_testcases(tmp_path)
    monkeypatch.chdir(repo)
    health = tmp_path / "health.json"
    runner = CliRunner()
    result = runner.invoke(main, [
        "audit", "--testcases-dir", str(tcs), "--json", str(health),
    ])
    # The sample testcase intentionally has no fix.diff, so audit should fail
    # while still producing the health snapshot and readable table.
    assert result.exit_code == 1
    assert "bench-0001" in result.output
    assert "no_fix_diff" in result.output
    data = json.loads(health.read_text(encoding="utf-8"))
    assert data["schema"] == "aigdbench/health/1"
    assert data["total"] == 1
    assert data["testcases"][0]["id"] == "bench-0001"


def test_smoke_cli_passes_for_folder_case_with_fix_diff(tmp_path):
    tcs = tmp_path / "testcases"
    tc = tcs / "folder-0001"; tc.mkdir(parents=True)
    (tc / "testcase.toml").write_text("""
[testcase]
id = "folder-0001"
category = "intent_translation"
source_kind = "folder"
task = "set attack"

[verifier]
type = "py_config"
entry = "expected.json"

[scoring]
mode = "fields"
""", encoding="utf-8")
    (tc / "expected.json").write_text(json.dumps({"fields": [
        {"name": "attack", "aliases": ["attack"], "files_glob": "**/*.json",
         "expected": 60, "tol": 1e-6, "base": 50, "must_differ_from_base": True},
    ]}), encoding="utf-8")
    baseline = tc / "baseline"; (baseline / "data").mkdir(parents=True)
    (baseline / "data" / "char.json").write_text(json.dumps({"attack": 50}) + "\n", encoding="utf-8")
    (tc / "fix.diff").write_text(
        "diff --git a/data/char.json b/data/char.json\n"
        "--- a/data/char.json\n"
        "+++ b/data/char.json\n"
        "@@ -1 +1 @@\n"
        '-{"attack": 50}\n'
        '+{"attack": 60}\n',
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(main, [
        "smoke", "--testcases-dir", str(tcs), "--testcase", "folder-0001",
    ])
    assert result.exit_code == 0, result.output
    assert "folder-0001" in result.output
    assert "OK" in result.output


def test_scaffold_cli_creates_folder_testcase(tmp_path):
    tcs = tmp_path / "testcases"
    runner = CliRunner()
    result = runner.invoke(main, [
        "scaffold", "--testcases-dir", str(tcs), "--id", "new-path-case",
        "--category", "behavior_logic", "--task", "Make the NPC follow a path",
    ])
    assert result.exit_code == 0, result.output
    tc = tcs / "new-path-case"
    assert (tc / "testcase.toml").exists()
    assert (tc / "baseline" / ".gitkeep").exists()
    assert (tc / "verifier_scene.tscn").exists()
    assert (tc / "verifier.gd").exists()
    manifest = (tc / "testcase.toml").read_text(encoding="utf-8")
    assert 'source_kind = "folder"' in manifest
    assert "Make the NPC follow a path" in manifest


def test_scaffold_cli_copies_source_project_without_godot_cache(tmp_path):
    source = tmp_path / "project"
    (source / ".git").mkdir(parents=True)
    (source / ".godot").mkdir()
    (source / "scenes").mkdir()
    (source / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (source / "scenes" / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")

    tcs = tmp_path / "testcases"
    runner = CliRunner()
    result = runner.invoke(main, [
        "scaffold", "--testcases-dir", str(tcs), "--id", "copied-case",
        "--source-project", str(source),
    ])
    assert result.exit_code == 0, result.output
    baseline = tcs / "copied-case" / "baseline"
    assert (baseline / "project.godot").exists()
    assert (baseline / "scenes" / "main.tscn").exists()
    assert not (baseline / ".git").exists()
    assert not (baseline / ".godot").exists()


def test_run_command_driver_requires_cmd(tmp_path, monkeypatch):
    repo, tcs = _repo_and_testcases(tmp_path)
    monkeypatch.chdir(repo)
    runner = CliRunner()
    result = runner.invoke(main, [
        "run", "--testcases-dir", str(tcs), "--driver", "command",
    ])
    assert result.exit_code == 0
    assert "harness-cmd" in result.output.lower()


def _folder_case(tcs, cid: str, base: int = 50, expected: int = 60):
    tc = tcs / cid; tc.mkdir(parents=True)
    (tc / "testcase.toml").write_text(f"""
[testcase]
id = "{cid}"
category = "intent_translation"
source_kind = "folder"
task = "set attack"

[verifier]
type = "py_config"
entry = "expected.json"

[scoring]
mode = "fields"
""", encoding="utf-8")
    (tc / "expected.json").write_text(json.dumps({"fields": [
        {"name": "attack", "aliases": ["attack"], "files_glob": "**/*.json",
         "expected": expected, "tol": 1e-6, "base": base, "must_differ_from_base": True},
    ]}), encoding="utf-8")
    baseline = tc / "baseline"; (baseline / "data").mkdir(parents=True)
    (baseline / "data" / "char.json").write_text(
        json.dumps({"attack": base}) + "\n", encoding="utf-8")


def _run_report(tcs, report, jobs, cmd):
    runner = CliRunner()
    result = runner.invoke(main, [
        "run", "--testcases-dir", str(tcs), "--driver", "command",
        "--harness-cmd", cmd, "--harness", "fake", "--timeout", "60",
        "--log-dir", str(tcs.parent / f"logs{jobs}"),
        "--report", str(report), "--jobs", str(jobs),
    ])
    assert result.exit_code == 0, result.output
    return json.loads(report.read_text(encoding="utf-8"))


def test_parallel_run_matches_serial(tmp_path):
    # --jobs 4 must produce identical per-testcase results (and order) to --jobs 1:
    # the whole point of the driver-per-testcase refactor is deterministic
    # parallelism, not a race that changes scores.
    tcs = tmp_path / "testcases"
    for i in range(6):
        _folder_case(tcs, f"case-{i:04d}")
    script = ("import pathlib, json; "
              "p=pathlib.Path('data/char.json'); "
              "p.write_text(json.dumps({'attack': 60})+chr(10))")
    cmd = f'{sys.executable} -c "{script}"'

    serial = _run_report(tcs, tmp_path / "serial.json", 1, cmd)
    parallel = _run_report(tcs, tmp_path / "parallel.json", 4, cmd)

    def key(rep):
        return [(t["testcase_id"], t["score"], t["failure_stage"])
                for t in rep["testcases"]]
    assert key(serial) == key(parallel)
    assert serial["mean_score"] == parallel["mean_score"] == 1.0
    # Order is preserved (input order), not completion order.
    assert [t["testcase_id"] for t in parallel["testcases"]] == \
        [f"case-{i:04d}" for i in range(6)]


def test_command_report_carries_ai_agent_context(tmp_path):
    # A stream-json harness must surface structured turns/tokens in the report so
    # the dashboard activity view lights up for our own runs (not just survey).
    tcs = tmp_path / "testcases"
    _folder_case(tcs, "case-events")
    # A fake harness in its own script file: editing the file AND printing a
    # stream-json event. A file avoids embedding JSON's double-quotes in the
    # --harness-cmd template (shlex would mangle nested quotes).
    harness_py = tmp_path / "fake_harness.py"
    harness_py.write_text(
        "import pathlib, json\n"
        "pathlib.Path('data/char.json').write_text(json.dumps({'attack': 60}) + '\\n')\n"
        "event = {'type': 'assistant', 'message': {"
        "'content': [{'type': 'tool_use', 'name': 'Edit', "
        "'input': {'file_path': 'data/char.json'}}], "
        "'usage': {'input_tokens': 10, 'output_tokens': 5}}}\n"
        "print(json.dumps(event))\n",
        encoding="utf-8",
    )
    cmd = f'{sys.executable} {str(harness_py).replace(chr(92), "/")}'
    rep = _run_report(tcs, tmp_path / "ev.json", 1, cmd)
    tc0 = rep["testcases"][0]
    assert tc0["score"] == 1.0, tc0
    assert "ai_agent_context" in tc0
    ctx = tc0["ai_agent_context"]
    assert ctx["total_tokens"] == 15
    assert ctx["turns"] and ctx["turns"][0]["tool_calls"] == ["Edit(data/char.json)"]


def test_compare_cli_reports_significance(tmp_path):
    # compare must align two reports on shared testcases and print a paired diff
    # with a p-value; here A strictly dominates B, so it should read significant.
    def _rep(path, harness, scores):
        tcs = [{"testcase_id": f"t{i}", "category": "behavior_logic", "score": s}
               for i, s in enumerate(scores)]
        path.write_text(json.dumps({
            "harness": harness, "count": len(tcs),
            "mean_score": sum(scores) / len(scores), "testcases": tcs,
        }), encoding="utf-8")

    a = tmp_path / "a.json"; b = tmp_path / "b.json"
    _rep(a, "alpha", [1.0, 1.0, 1.0, 1.0, 1.0])
    _rep(b, "beta", [0.0, 0.0, 0.0, 0.0, 0.0])
    runner = CliRunner()
    result = runner.invoke(main, [
        "compare", "--report-a", str(a), "--report-b", str(b),
        "--iters", "1000", "--seed", "0",
    ])
    assert result.exit_code == 0, result.output
    assert "alpha" in result.output and "beta" in result.output
    assert "5 shared testcase" in result.output
    assert "significant" in result.output
    assert "favors alpha" in result.output


def test_compare_cli_no_common_testcases(tmp_path):
    def _rep(path, harness, ids):
        tcs = [{"testcase_id": i, "category": "behavior_logic", "score": 1.0} for i in ids]
        path.write_text(json.dumps({"harness": harness, "count": len(tcs),
                                    "mean_score": 1.0, "testcases": tcs}), encoding="utf-8")
    a = tmp_path / "a.json"; b = tmp_path / "b.json"
    _rep(a, "alpha", ["x", "y"])
    _rep(b, "beta", ["p", "q"])
    result = CliRunner().invoke(main, ["compare", "--report-a", str(a), "--report-b", str(b)])
    assert result.exit_code == 0
    assert "No common testcases" in result.output


def _flaky_harness(tmp_path, counter):
    """A fake harness whose success alternates by call count: it increments a
    shared counter file and only sets attack=60 on ODD-numbered calls, so over N
    attempts exactly ceil(N/2) pass. Deterministic distribution to assert on."""
    h = tmp_path / "flaky_harness.py"
    h.write_text(
        "import pathlib, json\n"
        f"c = pathlib.Path(r'{counter}'.replace(chr(92), '/'))\n"
        "n = int(c.read_text()) if c.exists() else 0\n"
        "n += 1\n"
        "c.write_text(str(n))\n"
        "if n % 2 == 1:\n"
        "    pathlib.Path('data/char.json').write_text(json.dumps({'attack': 60}) + '\\n')\n",
        encoding="utf-8",
    )
    return f'{sys.executable} {str(h).replace(chr(92), "/")}'


def test_repeat_builds_distribution_and_mean_score(tmp_path):
    # --repeat 4 must attach a `repeat` block with the per-attempt scores and set
    # the record's score to their mean, so a stochastic harness is measurable.
    tcs = tmp_path / "testcases"
    _folder_case(tcs, "case-flaky")
    counter = tmp_path / "count.txt"
    cmd = _flaky_harness(tmp_path, counter)
    runner = CliRunner()
    report = tmp_path / "rep.json"
    result = runner.invoke(main, [
        "run", "--testcases-dir", str(tcs), "--driver", "command",
        "--harness-cmd", cmd, "--harness", "flaky", "--timeout", "60",
        "--log-dir", str(tmp_path / "logs"),
        "--report", str(report), "--repeat", "4",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["repeat"] == 4
    tc0 = data["testcases"][0]
    rpt = tc0["repeat"]
    assert rpt["n"] == 4
    assert len(rpt["scores"]) == 4
    # Odd calls (1st, 3rd) pass -> 2 of 4 -> mean 0.5.
    assert sorted(rpt["scores"]) == [0.0, 0.0, 1.0, 1.0]
    assert tc0["score"] == 0.5 == rpt["mean"]
    assert rpt["pass_at_1"] == 0.5
    assert rpt["std"] > 0.0
    # The suite CI is written at the top level too.
    assert "mean_score_ci95" in data
    # Failure funnel counts every attempt, so the two no-change attempts show.
    assert rpt["failure_stages"].get("no_change") == 2


def test_repeat_one_is_backward_compatible(tmp_path):
    # --repeat 1 must produce the exact same record shape as before: no `repeat`
    # block, score is the single attempt's score.
    tcs = tmp_path / "testcases"
    _folder_case(tcs, "case-single")
    script = ("import pathlib, json; "
              "pathlib.Path('data/char.json').write_text(json.dumps({'attack':60})+chr(10))")
    cmd = f'{sys.executable} -c "{script}"'
    data = _run_report(tcs, tmp_path / "one.json", 1, cmd)
    assert data.get("repeat") == 1
    tc0 = data["testcases"][0]
    assert "repeat" not in tc0
    assert tc0["score"] == 1.0
