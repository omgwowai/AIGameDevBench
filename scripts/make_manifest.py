#!/usr/bin/env python3
"""Generate manifest.json — the single source of truth for testcases_filtered/.

Also enforces dataset governance constraints:
  - single verifier type  <= 40% of cases
  - single source repo    <= 20% of cases  (authored:* exempt)
Run with --check to exit 1 on constraint violations (CI gate).
"""
import json, sys, tomllib
from collections import Counter
from pathlib import Path

def source_key(repo: str) -> str:
    if repo.startswith("authored"): return "authored"
    return repo.split(" ")[0]

def build(root: Path) -> dict:
    cases = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        t = tomllib.loads((d / "testcase.toml").read_text(encoding="utf-8"))
        cases.append({
            "id": t["testcase"]["id"],
            "category": t["testcase"]["category"],
            "info_level": t["testcase"].get("info_level"),
            "source_kind": t["testcase"]["source_kind"],
            "source_repo": t["testcase"].get("source_repo", ""),
            "verifier": t["verifier"]["type"],
            "scoring_mode": t.get("scoring", {}).get("mode"),
            "godot_version": t["testcase"].get("godot_version"),
            # calibration placeholders — filled by scripts/calibrate.py runs
            "difficulty": t["testcase"].get("difficulty"),
            "reference_pass_rate": None,
        })
    n = len(cases)
    dist = lambda k: dict(Counter(c[k] for c in cases).most_common())
    manifest = {
        "schema": "aigdbench.manifest.v1",
        "count": n,
        "distribution": {
            "category": dist("category"),
            "verifier": dist("verifier"),
            "scoring_mode": dist("scoring_mode"),
            "info_level": dist("info_level"),
            "source_repo": dict(Counter(source_key(c["source_repo"]) for c in cases).most_common()),
        },
        "constraint_violations": [],
        "scoring_note": "gated (binary 0/1) cases must be reported separately from graded modes; do not average across modes without stratification.",
        "cases": cases,
    }
    for v, cnt in manifest["distribution"]["verifier"].items():
        if cnt / n > 0.40:
            manifest["constraint_violations"].append(
                f"verifier '{v}' = {cnt}/{n} ({cnt/n:.0%}) exceeds 40% cap")
    for r, cnt in manifest["distribution"]["source_repo"].items():
        if r != "authored" and cnt / n > 0.20:
            manifest["constraint_violations"].append(
                f"source repo '{r}' = {cnt}/{n} ({cnt/n:.0%}) exceeds 20% cap")
    return manifest

if __name__ == "__main__":
    root = Path("testcases_filtered")
    m = build(root)
    out = Path("testcases_filtered_manifest.json")
    out.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}: {m['count']} cases")
    for v in m["constraint_violations"]:
        print("VIOLATION:", v)
    if "--check" in sys.argv and m["constraint_violations"]:
        sys.exit(1)
