#!/usr/bin/env python3
"""Aggregate per-testcase JSON reports (one per container/Job) into a single
combined report plus a Markdown summary and a printed table.

Each container writes ``<results>/<testcase_id>.json`` in the format produced
by ``aigdbench run --report`` (a dict with ``testcases: [...]``). This script
collects them all, flattens per-testcase records, and computes an overall mean.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys


def load_records(results_dir: str) -> list[dict]:
    records: list[dict] = []
    combined = os.path.join(results_dir, "report.json")
    for path in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        if os.path.abspath(path) == os.path.abspath(combined):
            continue  # never re-ingest our own output
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except (json.JSONDecodeError, OSError) as e:
            records.append({
                "testcase_id": os.path.splitext(os.path.basename(path))[0],
                "score": 0.0, "status": "error",
                "error": f"unreadable report: {e}",
            })
            continue
        for tc in data.get("testcases", [data]):
            vr = tc.get("verifier_result", {}) or {}
            records.append({
                "testcase_id": tc.get("testcase_id", "?"),
                "category": tc.get("category", ""),
                "score": float(tc.get("score", 0.0) or 0.0),
                "status": vr.get("status", tc.get("status", "?")),
                "l0_l1_pass": tc.get("l0_l1_pass"),
                "wall_time": tc.get("wall_time"),
                "exit_code": tc.get("exit_code"),
                "error": vr.get("error") or tc.get("error"),
                "log": tc.get("log"),
            })
    # De-dup by testcase_id (keep the highest score if a dupe slipped in).
    best: dict[str, dict] = {}
    for r in records:
        cur = best.get(r["testcase_id"])
        if cur is None or r["score"] > cur["score"]:
            best[r["testcase_id"]] = r
    return sorted(best.values(), key=lambda r: r["testcase_id"])


def _by_category(records: list[dict]) -> dict[str, dict]:
    cats: dict[str, dict] = {}
    for r in records:
        c = r.get("category") or "(uncategorized)"
        agg = cats.setdefault(c, {"n": 0, "sum": 0.0, "passed": 0})
        agg["n"] += 1
        agg["sum"] += r["score"]
        if r["score"] >= 1.0:
            agg["passed"] += 1
    return cats


def render_markdown(records: list[dict], driver: str, mean: float,
                    meta: dict | None = None) -> str:
    passed = sum(1 for r in records if r["score"] >= 1.0)
    meta = meta or {}
    lines = [
        "# AIGameDevBench — cluster matrix report",
        "",
        f"- driver: `{driver}`",
    ]
    if meta.get("image"):
        lines.append(f"- image: `{meta['image']}`")
    if meta.get("harness_cmd"):
        lines.append(f"- harness: `{meta['harness_cmd']}`")
    if meta.get("namespace"):
        lines.append(f"- k8s namespace: `{meta['namespace']}`")
    lines += [
        f"- testcases: **{len(records)}**",
        f"- passed (score = 1.0): **{passed}/{len(records)}**",
        f"- mean score: **{mean:.3f}**",
        "",
        "## Per-category",
        "",
        "| category | passed | n | mean |",
        "|---|:--:|:--:|---:|",
    ]
    for cat, agg in sorted(_by_category(records).items()):
        cmean = agg["sum"] / agg["n"] if agg["n"] else 0.0
        lines.append(f"| {cat} | {agg['passed']}/{agg['n']} | {agg['n']} | {cmean:.3f} |")
    lines += [
        "",
        "## Per-testcase",
        "",
        "| testcase | category | status | score | gate | notes |",
        "|---|---|---|---:|:--:|---|",
    ]
    for r in records:
        gate = "✓" if r.get("l0_l1_pass") else ("—" if r.get("l0_l1_pass") is None else "✗")
        note = (r.get("error") or "").replace("\n", " ")[:60]
        lines.append(
            f"| {r['testcase_id']} | {r.get('category','')} | {r['status']} "
            f"| {r['score']:.2f} | {gate} | {note} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--driver", default="")
    ap.add_argument("--image", default="")
    ap.add_argument("--harness-cmd", dest="harness_cmd", default="")
    ap.add_argument("--namespace", default="")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--markdown", dest="md_out", default=None)
    args = ap.parse_args()

    records = load_records(args.results_dir)
    mean = sum(r["score"] for r in records) / len(records) if records else 0.0
    passed = sum(1 for r in records if r["score"] >= 1.0)
    meta = {"image": args.image, "harness_cmd": args.harness_cmd,
            "namespace": args.namespace}

    report = {
        "driver": args.driver,
        "image": args.image or None,
        "harness_cmd": args.harness_cmd or None,
        "namespace": args.namespace or None,
        "count": len(records),
        "passed": passed,
        "mean_score": mean,
        "testcases": records,
    }

    # Printed table (stdout).
    print(f"{'testcase':30} {'status':10} {'score':>6}")
    print("-" * 50)
    for r in records:
        print(f"{r['testcase_id']:30} {str(r['status']):10} {r['score']:6.2f}")
    print("-" * 50)
    print(f"{'MEAN':30} {'':10} {mean:6.3f}   ({passed}/{len(records)} passed)")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nwrote {args.json_out}")
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as f:
            f.write(render_markdown(records, args.driver, mean, meta))
        print(f"wrote {args.md_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
