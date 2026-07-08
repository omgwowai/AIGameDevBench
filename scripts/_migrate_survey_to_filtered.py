#!/usr/bin/env python3
"""One-off: migrate selected survey (git-history) bad-case testcases into the
filtered set as offline, runnable cases backed by shared project snapshots.

Why: the survey `survey-history_*` cases are real bug-fix commits mined from
public Godot repos, and are higher-discrimination than some authored folder
cases — but as-shipped they are unrunnable (Windows temp source_repo, empty
bad.diff). This script rewrites each chosen case into a folder-type filtered
testcase that:
  - references a shared snapshot (repo tree at the fix commit's pre-image, i.e.
    the buggy state), generated on demand by scripts/make_snapshots.py;
  - scores with the `gated` mode so noop=0 / golden(good.diff)=1;
  - keeps survey_bad_case.json as the verifier oracle and good.diff as golden.

The base commit is the short hash embedded in the case id
(survey-history_<date>_<hash>_<n>) — verified to be exactly the commit whose diff
is good.diff (the fix). We snapshot THAT commit's tree (the pre-fix / buggy code).

Run from repo root:
    python3 scripts/_migrate_survey_to_filtered.py

It only writes files; run scripts/make_snapshots.py afterwards to materialise the
project trees, then `aigdbench audit --testcases-dir testcases_filtered`.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _survey_repos import resolve_repo_url, repo_name_from_source  # noqa: E402

# The 4 selected cases (all godot-open-rpg, runtime-L0 oracle, focused diffs).
# Whittled from 16 discriminating candidates: these are the ones whose golden
# (good.diff) cleanly passes the L0/L1 gate under real Godot (verified via the
# container diagnostic). The other 12 either fail L0 on the project's own main/
# other scenes (real pre-existing crashes, unrelated to the case's bug) or don't
# trip the gated action check — see docs/filtered_dataset_report.md.
SELECTED = [
    "survey-history_2023-06-09_73dc20c_000",
    "survey-history_2023-08-21_9cbd293_003",
    "survey-history_2025-09-19_019b51c_009",
    "survey-history_2026-01-26_1406822_013",
]

SRC_ROOT = Path("testcases")
DST_ROOT = Path("testcases_filtered")

_ID_RE = re.compile(r"^survey-history_[0-9-]+_([0-9a-f]+)_[0-9]+$")


def _id_short_hash(cid: str) -> str:
    m = _ID_RE.match(cid)
    if not m:
        raise ValueError(f"cannot extract base commit hash from id '{cid}'")
    return m.group(1)


def _manifest(cid: str, category: str, task: str, snapshot: str) -> str:
    # TOML with a multi-line task string; task is escaped minimally (it must not
    # contain triple-quotes — survey tasks don't).
    return (
        '[testcase]\n'
        f'id = "{cid}"\n'
        f'category = "{category}"\n'
        'source_kind = "folder"\n'
        f'snapshot = "{snapshot}"\n'
        'source_repo = "survey:godot-open-rpg (offline snapshot)"\n'
        f'task = """\n{task.strip()}\n"""\n\n'
        '[verifier]\n'
        'type = "survey_bad_case"\n'
        'entry = "survey_bad_case.json"\n\n'
        '[scoring]\n'
        'mode = "gated"\n\n'
        '[provenance]\n'
        'source = "survey-history (migrated to filtered)"\n'
        'notes = "Real bug-fix commit from gdquest-demos/godot-open-rpg. Base '
        'snapshot = the fix commit\'s pre-image (buggy state). gated scoring: '
        'noop=0, good.diff=1. Snapshot generated on demand by '
        'scripts/make_snapshots.py."\n'
    )


def main() -> int:
    made = 0
    for cid in SELECTED:
        src = SRC_ROOT / cid
        spec_path = src / "survey_bad_case.json"
        if not spec_path.exists():
            print(f"! {cid}: no survey_bad_case.json — skipped")
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        toml = (src / "testcase.toml").read_text(encoding="utf-8")
        # Pull category + task out of the original manifest via the loader-free
        # path: parse minimally (they are simple lines / one triple-quoted block).
        cat_m = re.search(r'category\s*=\s*"([^"]+)"', toml)
        category = cat_m.group(1) if cat_m else "behavior_logic"
        task_m = re.search(r'task\s*=\s*"""(.*?)"""', toml, re.S)
        if not task_m:
            task_m = re.search(r'task\s*=\s*"([^"]*)"', toml, re.S)
        task = task_m.group(1) if task_m else "Fix the defect in the affected file(s)."

        repo_name = repo_name_from_source(spec.get("source_repo", "godot-open-rpg"))
        url = resolve_repo_url(spec.get("source_repo", "godot-open-rpg"))
        if not url:
            print(f"! {cid}: no public URL for repo '{repo_name}' — skipped")
            continue
        base_ref = _id_short_hash(cid)
        snapshot = f"{repo_name}__{base_ref}"

        dst = DST_ROOT / cid
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)

        # Verifier oracle + golden + (empty) bad diff.
        shutil.copy2(spec_path, dst / "survey_bad_case.json")
        shutil.copy2(src / "good.diff", dst / "good.diff")
        bad_src = src / "bad.diff"
        (dst / "bad.diff").write_text(
            bad_src.read_text(encoding="utf-8") if bad_src.exists() else "",
            encoding="utf-8")

        (dst / "testcase.toml").write_text(
            _manifest(cid, category, task, snapshot), encoding="utf-8")

        # snapshot.json drives scripts/make_snapshots.py (repo + base commit).
        (dst / "snapshot.json").write_text(json.dumps({
            "snapshot": snapshot,
            "repo_url": url,
            "base_ref": base_ref,
            "note": "base_ref is the fix commit (pre-image is its tree); good.diff "
                    "applies cleanly on it and is the golden fix.",
        }, indent=2), encoding="utf-8")

        print(f"+ {cid}  snapshot={snapshot}  golden=good.diff  scoring=gated")
        made += 1

    print(f">>> migrated {made} case(s) into {DST_ROOT}/")
    print(">>> next: python3 scripts/make_snapshots.py --testcases-dir testcases_filtered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
