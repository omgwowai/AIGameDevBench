#!/usr/bin/env python3
"""Generate on-demand project snapshots for git-derived filtered testcases.

Some testcases in `testcases_filtered/` are real bug-fix cases mined from public
Godot repos. To keep the repo small they do NOT vendor a full project tree;
instead each references a shared snapshot under `testcases_filtered/_snapshots/`
(or `testcases/_snapshots/`), which is gitignored and generated locally by THIS
script the first time you want to run those cases. After generation the cases run
fully offline.

For each such testcase this reads `snapshot.json` (written by
scripts/_migrate_survey_to_filtered.py), which records the public repo and the
exact base commit (the fix commit's pre-image — the buggy state the harness must
fix). It clones the repo once into a local cache, then `git archive`s the base
commit into the snapshot dir (no .git, no Godot import cache — those regenerate).

Usage:
    python3 scripts/make_snapshots.py [--testcases-dir testcases_filtered]
                                      [--cache ~/.cache/aigdbench/repos]
                                      [--force]

Requires network access for the initial clone only. Idempotent: existing
snapshots are skipped unless --force.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _run(args: list[str], cwd: Path | None = None) -> str:
    r = subprocess.run(args, cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def _ensure_clone(url: str, cache_dir: Path) -> Path:
    """Clone `url` into cache_dir/<name> once; fetch if already present."""
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    dest = cache_dir / name
    if (dest / ".git").is_dir():
        try:
            _run(["git", "fetch", "--quiet", "--all", "--tags"], cwd=dest)
        except RuntimeError as e:
            print(f"    (fetch failed, using existing clone: {e})")
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"    cloning {url} -> {dest}")
    _run(["git", "clone", "--quiet", url, str(dest)])
    return dest


# Editor-only test-framework addons that some repos vendor. They declare many
# `class_name`s but are never referenced by the game's runtime scenes, so they
# only bloat the snapshot and — because they don't all compile headlessly —
# leave the global class cache incomplete, which trips the godot_scene_assert
# import-cache guard. Strip them from snapshots; the game runs fine without them.
_STRIP_ADDONS = ("addons/gut", "addons/gdUnit4")


def _make_snapshot(repo: Path, ref: str, out_dir: Path) -> None:
    """Export `ref`'s tree into out_dir (no .git). Uses git archive piped to tar."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # git archive <ref> | tar -x -C out_dir
    p1 = subprocess.Popen(["git", "archive", "--format=tar", ref],
                          cwd=str(repo), stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["tar", "-x", "-C", str(out_dir)], stdin=p1.stdout)
    p1.stdout.close()
    p2.communicate()
    if p2.returncode != 0 or p1.wait() != 0:
        raise RuntimeError(f"archive/extract failed for {ref}")
    # Drop editor-only test addons that would leave the class cache incomplete.
    for rel in _STRIP_ADDONS:
        addon_dir = out_dir / rel
        if addon_dir.is_dir():
            shutil.rmtree(addon_dir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--testcases-dir", default="testcases_filtered")
    ap.add_argument("--cache", default=str(Path.home() / ".cache/aigdbench/repos"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    tc_root = Path(args.testcases_dir)
    cache_dir = Path(args.cache).expanduser()
    snap_root = tc_root / "_snapshots"

    # Collect (snapshot_name, url, ref) from every testcase carrying snapshot.json.
    jobs: dict[str, tuple[str, str]] = {}
    for meta_path in sorted(tc_root.glob("*/snapshot.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        name = meta["snapshot"]
        jobs.setdefault(name, (meta["repo_url"], meta["base_ref"]))

    if not jobs:
        print(f"no snapshot.json found under {tc_root}/*/ — nothing to do")
        return 0

    print(f">>> {len(jobs)} snapshot(s) to ensure under {snap_root}/")
    clones: dict[str, Path] = {}
    made = skipped = 0
    for name, (url, ref) in jobs.items():
        out_dir = snap_root / name
        if out_dir.is_dir() and any(out_dir.iterdir()) and not args.force:
            print(f"  = {name} (exists, skip)")
            skipped += 1
            continue
        if url not in clones:
            clones[url] = _ensure_clone(url, cache_dir)
        repo = clones[url]
        # Resolve ref to a full commit so a short hash / rebased ref is caught early.
        try:
            _run(["git", "cat-file", "-e", f"{ref}^{{commit}}"], cwd=repo)
        except RuntimeError:
            print(f"  ! {name}: ref {ref} not found in {url} — SKIPPED")
            continue
        print(f"  + {name} (archive {ref})")
        _make_snapshot(repo, ref, out_dir)
        made += 1

    print(f">>> done: {made} made, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
