from __future__ import annotations

import json
from pathlib import Path

from aigamedevbench.webreport import (
    load_reports, build_summary, report_detail, load_testcase_catalog,
    load_testcase_detail,
)


def _write_report(path: Path, harness: str, testcases: list[dict]) -> None:
    count = len(testcases)
    mean = sum(t["score"] for t in testcases) / count if count else 0.0
    path.write_text(
        json.dumps({
            "harness": harness,
            "count": count,
            "mean_score": mean,
            "testcases": testcases,
        }),
        encoding="utf-8",
    )


def _tc(tcid: str, score: float, category: str = "behavior_logic",
        checks: list[dict] | None = None, **extra) -> dict:
    vr = {
        "score": score,
        "status": "pass" if score >= 1.0 else ("fail" if score <= 0 else "partial"),
        "category": category,
        "error": "",
        "checks": checks or [],
    }
    rec = {
        "testcase_id": tcid,
        "harness": "h",
        "category": category,
        "l0_l1_pass": True,
        "score": score,
        "verifier_result": vr,
        "diff": "",
        "artifacts_path": None,
    }
    rec.update(extra)
    return rec


def test_load_reports_reads_each_json_and_injects_run_metadata(tmp_path):
    _write_report(tmp_path / "report_a.json", "alpha", [_tc("t1", 1.0)])
    _write_report(tmp_path / "report_b.json", "beta", [_tc("t1", 0.0)])

    reports = load_reports(tmp_path)

    assert len(reports) == 2
    by_file = {r["_file"]: r for r in reports}
    assert by_file["report_a.json"]["harness"] == "alpha"
    assert by_file["report_b.json"]["harness"] == "beta"
    for r in reports:
        assert isinstance(r["_mtime"], float)
        assert r["_run_id"]  # non-empty unique id


def test_load_reports_run_ids_are_unique_even_with_same_harness(tmp_path):
    _write_report(tmp_path / "r1.json", "claude", [_tc("t1", 1.0)])
    _write_report(tmp_path / "r2.json", "claude", [_tc("t1", 0.5)])

    run_ids = {r["_run_id"] for r in load_reports(tmp_path)}

    assert len(run_ids) == 2


def test_load_reports_skips_bad_and_non_report_json(tmp_path):
    _write_report(tmp_path / "good.json", "ok", [_tc("t1", 1.0)])
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    (tmp_path / "other.json").write_text(json.dumps({"foo": "bar"}), encoding="utf-8")

    reports = load_reports(tmp_path)

    assert [r["_file"] for r in reports] == ["good.json"]


def test_build_summary_score_matrix_fills_none_for_missing_testcase(tmp_path):
    _write_report(tmp_path / "a.json", "alpha", [_tc("t1", 1.0), _tc("t2", 0.5)])
    _write_report(tmp_path / "b.json", "beta", [_tc("t1", 0.0)])

    summary = build_summary(load_reports(tmp_path))

    assert summary["testcases"] == ["t1", "t2"]
    a = next(r["_run_id"] for r in load_reports(tmp_path) if r["_file"] == "a.json")
    b = next(r["_run_id"] for r in load_reports(tmp_path) if r["_file"] == "b.json")
    matrix = summary["matrix"]
    assert matrix["t1"][a] == 1.0
    assert matrix["t1"][b] == 0.0
    assert matrix["t2"][a] == 0.5
    assert matrix["t2"][b] is None


def test_build_summary_time_matrix_carries_per_task_wall_time(tmp_path):
    # The Time view of the matrix reads summary["matrix_time"]: testcase x run
    # -> harness wall_time (s). Cells with no wall_time (or missing testcase)
    # must be None so the UI renders "-" rather than 0.
    _write_report(tmp_path / "a.json", "alpha",
                  [_tc("t1", 1.0, wall_time=39.8), _tc("t2", 0.5, wall_time=51.2)])
    _write_report(tmp_path / "b.json", "beta",
                  [_tc("t1", 0.0, wall_time=48.7), _tc("t2", 0.0)])  # t2: no wall_time

    reports = load_reports(tmp_path)
    summary = build_summary(reports)
    a = next(r["_run_id"] for r in reports if r["_file"] == "a.json")
    b = next(r["_run_id"] for r in reports if r["_file"] == "b.json")

    mt = summary["matrix_time"]
    assert mt["t1"][a] == 39.8
    assert mt["t1"][b] == 48.7
    assert mt["t2"][a] == 51.2
    assert mt["t2"][b] is None  # wall_time absent -> None, not 0


def test_build_summary_run_meta_carries_mean_and_count(tmp_path):
    _write_report(tmp_path / "a.json", "alpha", [_tc("t1", 1.0), _tc("t2", 0.0)])

    summary = build_summary(load_reports(tmp_path))

    run = summary["runs"][0]
    assert run["harness"] == "alpha"
    assert run["count"] == 2
    assert run["mean_score"] == 0.5
    assert isinstance(run["mtime"], float)


def test_build_summary_category_aggregate(tmp_path):
    _write_report(tmp_path / "a.json", "alpha", [
        _tc("t1", 1.0, category="behavior_logic"),
        _tc("t2", 0.0, category="behavior_logic"),
        _tc("t3", 1.0, category="visual"),
    ])

    summary = build_summary(load_reports(tmp_path))
    run_id = summary["runs"][0]["run_id"]
    cats = summary["categories"][run_id]

    assert cats["behavior_logic"]["mean"] == 0.5
    assert cats["behavior_logic"]["pass_rate"] == 0.5
    assert cats["visual"]["mean"] == 1.0
    assert cats["visual"]["pass_rate"] == 1.0


def test_build_summary_timing_aggregate(tmp_path):
    _write_report(tmp_path / "a.json", "alpha", [
        _tc("t1", 1.0, wall_time=2.0, timed_out=False),
        _tc("t2", 0.0, wall_time=4.0, timed_out=True),
    ])

    summary = build_summary(load_reports(tmp_path))
    run_id = summary["runs"][0]["run_id"]
    timing = summary["timing"][run_id]

    assert timing["total_wall_time"] == 6.0
    assert timing["mean_wall_time"] == 3.0
    assert timing["timed_out"] == 1
    assert timing["stalled"] == 0
    assert timing["blocked_on_approval"] == 0


def test_report_detail_returns_checks_with_expected_actual(tmp_path):
    checks = [
        {"name": "c1", "passed": True, "detail": "ok", "expected": 1, "actual": 1},
        {"name": "c2", "passed": False, "detail": "bad", "expected": 2, "actual": 3},
    ]
    _write_report(tmp_path / "a.json", "alpha",
                  [_tc("t1", 0.5, checks=checks, log_path="harness-logs/x.log")])

    reports = load_reports(tmp_path)
    detail = report_detail(reports[0], "t1")

    assert detail["testcase_id"] == "t1"
    assert detail["checks"][1]["expected"] == 2
    assert detail["checks"][1]["actual"] == 3
    assert detail["log_path"] == "harness-logs/x.log"


def test_report_detail_missing_testcase_returns_none(tmp_path):
    _write_report(tmp_path / "a.json", "alpha", [_tc("t1", 1.0)])
    reports = load_reports(tmp_path)

    assert report_detail(reports[0], "nope") is None


def test_report_detail_includes_ai_turns_from_agent_context(tmp_path):
    ctx = {
        "harness": "codex",
        "total_tokens": 1234,
        "turns": [
            {
                "turn": 2,
                "agent_input": "make it blue",
                "agent_output": "changed the color track",
                "tool_calls": ["shell_command:git commit -m x"],
            }
        ],
    }
    _write_report(tmp_path / "a.json", "survey",
                  [_tc("s1", 0.0, ai_agent_context=ctx)])

    detail = report_detail(load_reports(tmp_path)[0], "s1")

    assert len(detail["ai_turns"]) == 1
    t = detail["ai_turns"][0]
    assert t["agent_input"] == "make it blue"
    assert t["agent_output"] == "changed the color track"
    assert t["tool_calls"] == ["shell_command:git commit -m x"]


def test_report_detail_reads_log_text_from_log_path(tmp_path):
    log_dir = tmp_path / "harness-logs"
    log_dir.mkdir()
    (log_dir / "x.log").write_text("line1\nline2\nline3\n", encoding="utf-8")
    _write_report(tmp_path / "a.json", "alpha",
                  [_tc("t1", 0.5, log_path="harness-logs/x.log")])

    detail = report_detail(load_reports(tmp_path)[0], "t1", reports_dir=tmp_path)

    assert "line2" in detail["log_text"]


def test_report_detail_log_text_empty_when_no_log(tmp_path):
    _write_report(tmp_path / "a.json", "alpha", [_tc("t1", 1.0)])

    detail = report_detail(load_reports(tmp_path)[0], "t1", reports_dir=tmp_path)

    assert detail["log_text"] == ""
    assert detail["ai_turns"] == []


def _write_testcase(root: Path, tcid: str, *, category: str = "behavior_logic",
                    task: str = "do the thing", verifier_type: str = "godot_scene_assert",
                    entry: str = "verifier_scene.tscn", scoring: str = "checkpoints",
                    source_kind: str = "folder", extra_files: dict | None = None) -> Path:
    d = root / tcid
    (d).mkdir(parents=True, exist_ok=True)
    toml = (
        "[testcase]\n"
        f'id = "{tcid}"\n'
        f'category = "{category}"\n'
        f'source_kind = "{source_kind}"\n'
        f'task = "{task}"\n'
        "\n[verifier]\n"
        f'type = "{verifier_type}"\n'
        f'entry = "{entry}"\n'
        "\n[scoring]\n"
        f'mode = "{scoring}"\n'
    )
    (d / "testcase.toml").write_text(toml, encoding="utf-8")
    for name, content in (extra_files or {}).items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def test_load_testcase_catalog_reads_each_testcase(tmp_path):
    _write_testcase(tmp_path, "tc-a", task="task A", category="behavior_logic")
    _write_testcase(tmp_path, "tc-b", task="task B", category="precise_edit")

    catalog = load_testcase_catalog(tmp_path)

    by_id = {tc["id"]: tc for tc in catalog}
    assert set(by_id) == {"tc-a", "tc-b"}
    assert by_id["tc-a"]["task"] == "task A"
    assert by_id["tc-a"]["category"] == "behavior_logic"
    assert by_id["tc-a"]["verifier_type"] == "godot_scene_assert"
    assert by_id["tc-a"]["scoring_mode"] == "checkpoints"
    assert by_id["tc-b"]["category"] == "precise_edit"


def test_load_testcase_catalog_is_sorted_by_id(tmp_path):
    _write_testcase(tmp_path, "tc-z")
    _write_testcase(tmp_path, "tc-a")
    _write_testcase(tmp_path, "tc-m")

    catalog = load_testcase_catalog(tmp_path)

    assert [tc["id"] for tc in catalog] == ["tc-a", "tc-m", "tc-z"]


def test_load_testcase_catalog_lists_files(tmp_path):
    _write_testcase(tmp_path, "tc-a", extra_files={
        "fix.diff": "diff body",
        "baseline/project.godot": "[application]\n",
        "baseline/scripts/player.gd": "extends Node\n",
    })

    catalog = load_testcase_catalog(tmp_path)
    files = catalog[0]["files"]

    assert "testcase.toml" in files
    assert "fix.diff" in files
    assert "baseline/project.godot" in files
    assert "baseline/scripts/player.gd" in files


def test_load_testcase_catalog_omits_full_file_contents(tmp_path):
    _write_testcase(tmp_path, "tc-a", extra_files={"fix.diff": "diff body"})

    catalog = load_testcase_catalog(tmp_path)

    assert "file_contents" not in catalog[0]
    assert catalog[0]["files"] == ["fix.diff", "testcase.toml"]


def test_load_testcase_detail_includes_full_text_file_contents(tmp_path):
    _write_testcase(tmp_path, "tc-a", extra_files={
        "fix.diff": "diff body",
        "baseline/scripts/player.gd": "extends Node\n",
    })

    detail = load_testcase_detail(tmp_path, "tc-a")
    by_path = {f["path"]: f for f in detail["file_contents"]}
    script_path = tmp_path / "tc-a" / "baseline" / "scripts" / "player.gd"

    assert by_path["fix.diff"]["is_text"] is True
    assert by_path["fix.diff"]["content"] == "diff body"
    assert by_path["baseline/scripts/player.gd"]["content"] == script_path.read_bytes().decode("utf-8")
    assert detail["files"] == [f["path"] for f in detail["file_contents"]]


def test_load_testcase_detail_marks_binary_files_without_content(tmp_path):
    tc_dir = _write_testcase(tmp_path, "tc-a")
    (tc_dir / "sprite.bin").write_bytes(b"abc\x00def")

    detail = load_testcase_detail(tmp_path, "tc-a")
    by_path = {f["path"]: f for f in detail["file_contents"]}

    assert by_path["sprite.bin"]["is_text"] is False
    assert by_path["sprite.bin"]["content"] is None
    assert by_path["sprite.bin"]["size"] == 7

def test_load_testcase_catalog_skips_non_testcase_dirs(tmp_path):
    _write_testcase(tmp_path, "tc-a")
    (tmp_path / "not_a_testcase").mkdir()
    (tmp_path / "not_a_testcase" / "readme.txt").write_text("x", encoding="utf-8")
    (tmp_path / "loose.txt").write_text("x", encoding="utf-8")

    catalog = load_testcase_catalog(tmp_path)

    assert [tc["id"] for tc in catalog] == ["tc-a"]


def test_load_testcase_catalog_tolerates_bad_manifest(tmp_path):
    _write_testcase(tmp_path, "good")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "testcase.toml").write_text("this is not valid toml = = =", encoding="utf-8")

    catalog = load_testcase_catalog(tmp_path)

    assert [tc["id"] for tc in catalog] == ["good"]


def test_load_testcase_catalog_missing_dir_returns_empty(tmp_path):
    assert load_testcase_catalog(tmp_path / "nope") == []


def test_load_testcase_detail_missing_testcase_returns_none(tmp_path):
    _write_testcase(tmp_path, "tc-a")

    assert load_testcase_detail(tmp_path, "missing") is None


def test_repeat_block_surfaces_in_summary_and_detail(tmp_path):
    # A --repeat run's confidence interval and per-attempt distribution must reach
    # the dashboard: the run summary carries mean_score_ci95/repeat, and the
    # per-testcase detail carries the full repeat block.
    repeat_block = {
        "n": 4, "mean": 0.5, "std": 0.577, "stderr": 0.288,
        "ci95": [0.0, 1.0], "pass_at_1": 0.5,
        "scores": [1.0, 0.0, 1.0, 0.0],
        "failure_stages": {"none": 2, "no_change": 2},
    }
    tc = _tc("tc-flaky", 0.5, repeat=repeat_block)
    p = tmp_path / "report_repeat.json"
    p.write_text(json.dumps({
        "harness": "flaky", "count": 1, "repeat": 4, "mean_score": 0.5,
        "mean_score_ci95": [0.2, 0.8], "testcases": [tc],
    }), encoding="utf-8")

    reports = load_reports(tmp_path)
    summary = build_summary(reports)
    run = summary["runs"][0]
    assert run["repeat"] == 4
    assert run["mean_score_ci95"] == [0.2, 0.8]

    detail = report_detail(reports[0], "tc-flaky", reports_dir=tmp_path)
    assert detail["repeat"]["n"] == 4
    assert detail["repeat"]["scores"] == [1.0, 0.0, 1.0, 0.0]
    assert detail["repeat"]["pass_at_1"] == 0.5


def test_report_without_repeat_defaults_gracefully(tmp_path):
    # A pre-repeat report (no `repeat` key) must still work: run.repeat defaults
    # to 1, mean_score_ci95 is None, and detail.repeat is None.
    p = tmp_path / "report_old.json"
    _write_report(p, "old", [_tc("tc-a", 1.0)])
    reports = load_reports(tmp_path)
    run = build_summary(reports)["runs"][0]
    assert run["repeat"] == 1
    assert run["mean_score_ci95"] is None
    detail = report_detail(reports[0], "tc-a", reports_dir=tmp_path)
    assert detail["repeat"] is None
