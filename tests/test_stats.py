from __future__ import annotations

import math

from aigamedevbench.stats import (
    aggregate_scores, pass_at_k, combine_repeat_stages,
    paired_bootstrap, paired_scores,
)


def test_aggregate_empty():
    a = aggregate_scores([])
    assert a["n"] == 0 and a["mean"] == 0.0 and a["ci95"] == [0.0, 0.0]


def test_aggregate_single_has_no_spread():
    a = aggregate_scores([0.7])
    assert a["n"] == 1
    assert a["mean"] == 0.7
    assert a["std"] == 0.0 and a["stderr"] == 0.0
    # A single sample gives a degenerate CI equal to the mean.
    assert a["ci95"] == [0.7, 0.7]
    assert a["pass_at_1"] == 0.0  # 0.7 is not a full pass


def test_aggregate_all_pass():
    a = aggregate_scores([1.0, 1.0, 1.0])
    assert a["mean"] == 1.0
    assert a["std"] == 0.0
    assert a["pass_at_1"] == 1.0
    assert a["ci95"] == [1.0, 1.0]


def test_aggregate_all_fail():
    a = aggregate_scores([0.0, 0.0])
    assert a["mean"] == 0.0
    assert a["pass_at_1"] == 0.0
    assert a["ci95"] == [0.0, 0.0]


def test_aggregate_mixed_matches_manual():
    scores = [1.0, 1.0, 0.0, 1.0]  # mean 0.75
    a = aggregate_scores(scores)
    assert a["mean"] == 0.75
    # sample std (ddof=1): var = sum((x-0.75)^2)/3 = (0.0625*3 + 0.5625)/3 = 0.25
    assert math.isclose(a["std"], 0.5, rel_tol=1e-6)
    assert math.isclose(a["stderr"], 0.25, rel_tol=1e-6)
    assert a["pass_at_1"] == 0.75
    # CI clamped to [0,1]: 0.75 ± 1.96*0.25 -> upper clamps at 1.0
    assert a["ci95"][0] < 0.75 < 1.0
    assert a["ci95"][1] == 1.0


def test_ci_clamped_to_unit_interval():
    a = aggregate_scores([0.9, 1.0, 0.95, 1.0, 0.9])
    assert 0.0 <= a["ci95"][0] <= a["ci95"][1] <= 1.0


def test_pass_at_k_basic():
    # 2 of 4 attempts pass.
    scores = [1.0, 0.0, 1.0, 0.0]
    # pass@1 = c/n = 0.5
    assert math.isclose(pass_at_k(scores, 1), 0.5, rel_tol=1e-6)
    # pass@3: 1 - C(2,3)/C(4,3); C(2,3)=0 -> 1.0 (can't draw 3 all-failing)
    assert pass_at_k(scores, 3) == 1.0


def test_pass_at_k_edge_cases():
    assert pass_at_k([], 1) == 0.0
    assert pass_at_k([0.0, 0.0], 1) == 0.0          # no passes ever
    assert pass_at_k([1.0, 1.0], 5) == 1.0          # k clamped to n, all pass
    assert pass_at_k([1.0, 0.0], 0) == 0.0          # k<=0


def test_combine_repeat_stages():
    stages = ["none", "none", "no_change", "none", "harness_error"]
    counts = combine_repeat_stages(stages)
    assert counts == {"none": 3, "no_change": 1, "harness_error": 1}


# --- paired bootstrap (harness A vs B significance) ---

def test_paired_bootstrap_identical_is_not_significant():
    scores = [1.0, 0.5, 0.0, 1.0, 0.5]
    r = paired_bootstrap(scores, list(scores), iters=2000, seed=0)
    assert r["mean_diff"] == 0.0
    assert r["p_value"] == 1.0            # every resampled diff is exactly 0
    assert r["ci95"] == [0.0, 0.0]


def test_paired_bootstrap_large_gap_is_significant():
    a = [1.0] * 8
    b = [0.0] * 8
    r = paired_bootstrap(a, b, iters=2000, seed=0)
    assert r["mean_diff"] == 1.0
    assert r["p_value"] < 0.05            # CI excludes 0
    assert r["ci95"][0] > 0.0


def test_paired_bootstrap_is_reproducible_with_seed():
    a = [1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
    b = [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0]
    r1 = paired_bootstrap(a, b, iters=1500, seed=42)
    r2 = paired_bootstrap(a, b, iters=1500, seed=42)
    assert r1 == r2                        # deterministic under fixed seed
    r3 = paired_bootstrap(a, b, iters=1500, seed=7)
    # A different seed generally shifts the CI (not asserting exact inequality of
    # p, which can coincide, but the CI bounds should differ for this data).
    assert r3["ci95"] != r1["ci95"] or r3["p_value"] != r1["p_value"]


def test_paired_bootstrap_empty():
    r = paired_bootstrap([], [], iters=100, seed=0)
    assert r["n"] == 0 and r["p_value"] == 1.0


def test_paired_scores_aligns_on_common_ids():
    rep_a = {"testcases": [
        {"testcase_id": "x", "score": 1.0},
        {"testcase_id": "y", "score": 0.5},
        {"testcase_id": "only_a", "score": 0.2},
    ]}
    rep_b = {"testcases": [
        {"testcase_id": "y", "score": 0.0},
        {"testcase_id": "x", "score": 0.4},
        {"testcase_id": "only_b", "score": 0.9},
    ]}
    ids, sa, sb = paired_scores(rep_a, rep_b)
    assert ids == ["x", "y"]               # intersection, sorted
    assert sa == [1.0, 0.5]
    assert sb == [0.4, 0.0]
