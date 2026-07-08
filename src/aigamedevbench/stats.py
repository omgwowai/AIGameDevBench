from __future__ import annotations

"""Small, dependency-free statistics for --repeat runs.

An AI harness is stochastic: one pass over the suite is a single sample, so a
mean score alone cannot tell "harness A is better than B" apart from luck.
Running each testcase N times and reporting the spread (std / stderr / a 95%
confidence interval) and pass@k turns a point estimate into something you can
reason about statistically.

Pure stdlib on purpose (the whole repo avoids numpy/scipy): the CI uses a normal
approximation (mean ± 1.96·stderr), which is a deliberate simplification. For
small N or means near 0/1 it is only approximate — it is a spread indicator, not
a rigorous interval. Callers that need exact intervals should treat these as a
guide and bootstrap externally.
"""

import math
import random

_Z95 = 1.959963984540054  # z for a two-sided 95% normal interval


def aggregate_scores(scores: list[float]) -> dict:
    """Aggregate a list of per-attempt scores (each in [0, 1]) for one testcase.

    Returns mean, sample std (ddof=1), standard error, a normal-approx 95% CI
    clamped to [0, 1], and pass@1 (fraction of attempts that scored a full 1.0).
    An empty list yields all-zero stats; a single attempt has std=0 and a
    degenerate CI equal to the mean (one sample carries no spread information).
    """
    n = len(scores)
    if n == 0:
        return {"n": 0, "mean": 0.0, "std": 0.0, "stderr": 0.0,
                "ci95": [0.0, 0.0], "pass_at_1": 0.0}
    mean = sum(scores) / n
    if n == 1:
        std = 0.0
        stderr = 0.0
    else:
        # Sample variance with Bessel's correction (ddof=1): we are estimating
        # the harness's true mean from a sample, not describing a fixed population.
        var = sum((s - mean) ** 2 for s in scores) / (n - 1)
        std = math.sqrt(var)
        stderr = std / math.sqrt(n)
    half = _Z95 * stderr
    lo = max(0.0, mean - half)
    hi = min(1.0, mean + half)
    passes = sum(1 for s in scores if s >= 1.0)
    return {
        "n": n,
        "mean": round(mean, 6),
        "std": round(std, 6),
        "stderr": round(stderr, 6),
        "ci95": [round(lo, 6), round(hi, 6)],
        "pass_at_1": round(passes / n, 6),
    }


def pass_at_k(scores: list[float], k: int) -> float:
    """Probability that at least one of k independent attempts is a full pass,
    estimated from the observed attempts (unbiased estimator, as in HumanEval).

    With n attempts of which c are passes: pass@k = 1 - C(n-c, k) / C(n, k).
    Returns 0.0 if n == 0, and 1.0 if k > n-c would require more failures than
    exist (i.e. it is impossible to draw k all-failing attempts)."""
    n = len(scores)
    if n == 0 or k <= 0:
        return 0.0
    c = sum(1 for s in scores if s >= 1.0)
    if c == 0:
        return 0.0
    if k > n:
        k = n
    fails = n - c
    if fails < k:
        return 1.0  # cannot pick k all-failing attempts -> guaranteed a pass
    # 1 - C(fails, k) / C(n, k), computed with math.comb (exact integers).
    return round(1.0 - math.comb(fails, k) / math.comb(n, k), 6)


def paired_bootstrap(scores_a: list[float], scores_b: list[float],
                     iters: int = 10000, seed: int = 0) -> dict:
    """Paired bootstrap comparing harness A vs B on the SAME testcases.

    Given per-testcase paired scores (a_i, b_i), resample the paired differences
    d_i = a_i - b_i with replacement `iters` times, and report the observed mean
    difference, a 95% percentile confidence interval on it, and a two-sided
    bootstrap p-value for H0: mean_diff == 0.

    Pairing (same testcases, differences) removes cross-testcase difficulty
    variance, so it detects a real A-vs-B gap far better than comparing two
    independent means. Deterministic: a fixed-seed random.Random makes the
    result reproducible (the module-level random / Math.random are unavailable).

    Returns {n, mean_a, mean_b, mean_diff, ci95, p_value}. n == 0 (no common
    testcases) yields zeros and p_value 1.0.
    """
    n = len(scores_a)
    if n == 0 or n != len(scores_b):
        return {"n": 0, "mean_a": 0.0, "mean_b": 0.0, "mean_diff": 0.0,
                "ci95": [0.0, 0.0], "p_value": 1.0}
    diffs = [a - b for a, b in zip(scores_a, scores_b)]
    mean_diff = sum(diffs) / n
    rng = random.Random(seed)
    boot_means: list[float] = []
    # ge/le counts for a two-sided p-value: how often a resample's mean lands on
    # the opposite side of 0 from the observed effect (the standard "does the
    # bootstrap distribution straddle 0" test).
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        boot_means.append(s / n)
    boot_means.sort()
    lo = boot_means[int(0.025 * iters)]
    hi = boot_means[min(iters - 1, int(0.975 * iters))]
    # Two-sided p: 2 x the smaller tail mass beyond 0 (fraction of resamples with
    # the opposite sign to the observed mean_diff), clamped to 1.0.
    n_le0 = sum(1 for m in boot_means if m <= 0.0)
    n_ge0 = sum(1 for m in boot_means if m >= 0.0)
    p = min(1.0, 2.0 * min(n_le0, n_ge0) / iters)
    return {
        "n": n,
        "mean_a": round(sum(scores_a) / n, 6),
        "mean_b": round(sum(scores_b) / n, 6),
        "mean_diff": round(mean_diff, 6),
        "ci95": [round(lo, 6), round(hi, 6)],
        "p_value": round(p, 6),
    }


def paired_scores(report_a: dict, report_b: dict) -> tuple[list[str], list[float], list[float]]:
    """Align two reports by testcase_id (intersection, sorted). Returns
    (ids, scores_a, scores_b) so a paired test compares only shared testcases.
    Each report is the parsed JSON with a `testcases` list of {testcase_id, score}."""
    def _by_id(rep: dict) -> dict:
        out = {}
        for tc in rep.get("testcases", []):
            tid = tc.get("testcase_id")
            if tid is not None:
                out[tid] = float(tc.get("score", 0.0) or 0.0)
        return out
    a = _by_id(report_a)
    b = _by_id(report_b)
    ids = sorted(set(a) & set(b))
    return ids, [a[i] for i in ids], [b[i] for i in ids]


def combine_repeat_stages(stage_lists: list[str]) -> dict:
    """Count how the attempts distributed across failure stages, so a repeat
    block shows e.g. {"none": 5, "no_change": 1} — is the variance a flaky
    harness (no_change/harness_error) or a genuine borderline case?"""
    counts: dict[str, int] = {}
    for stage in stage_lists:
        counts[stage] = counts.get(stage, 0) + 1
    return counts
