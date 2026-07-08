#!/usr/bin/env bash
# compare-and-maybe-release.sh — after a benchmark batch finishes, compare its
# mean score against the agentic-game-development plugin's stored baseline (the
# score the CURRENT release was cut at). If the new run is better by > MIN_DELTA,
# bump the plugin version, refresh the baseline, and push to main — which triggers
# the repo's release-on-bump.yml to publish a new release fully automatically.
#
# Baseline is stored in TWO places (in-repo authoritative + release asset):
#   * $PLUGIN_REPO/workflow/benchmark-baseline.json   <- source of truth, versioned
#   * uploaded as an asset by release-on-bump.yml on each publish (for provenance)
# This script reads and writes the in-repo file only; the release asset is a copy.
#
# Decision (per operator choice): improvement => push to main = FULLY AUTOMATIC
# release (no PR gate). Guard rails: only runs with --auto-release; otherwise it
# just reports the comparison and what it WOULD do.
#
# Usage:
#   scripts/compare-and-maybe-release.sh \
#     --report results/<delivery>/report.json \
#     --plugin-repo ../agentic-game-development \
#     [--auto-release] [--min-delta 0] [--bump patch|minor|major] [--dry-run]
#
# Options (env in parens):
#   --report FILE     aggregate report.json from run_k8s_matrix.sh   (REPORT, required)
#   --plugin-repo DIR agentic-game-development checkout    (PLUGIN_REPO, default ../agentic-game-development)
#   --baseline FILE   baseline json path (rel to plugin repo)
#                                     (BASELINE_FILE, default workflow/benchmark-baseline.json)
#   --min-delta N     min mean-score gain to count as improvement    (MIN_DELTA, default 0)
#   --bump T          patch|minor|major version bump on release      (BUMP, default patch)
#   --auto-release    actually bump+commit+push main (else report only, no writes)
#   --delivery ID     delivery id, recorded in baseline provenance   (DELIVERY, optional)
#   --dry-run         do everything except git commit/push           (DRY_RUN)
#   -h                help
#
# Exit: 0 always on a clean comparison (improved or not). Non-zero only on error
# (missing report, malformed json, git failure).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

REPORT="${REPORT:-}"
PLUGIN_REPO="${PLUGIN_REPO:-$REPO_ROOT/../agentic-game-development}"
BASELINE_FILE="${BASELINE_FILE:-workflow/benchmark-baseline.json}"
MIN_DELTA="${MIN_DELTA:-0}"
BUMP="${BUMP:-patch}"
AUTO_RELEASE=0
DELIVERY="${DELIVERY:-}"
DRY_RUN="${DRY_RUN:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --report) REPORT="$2"; shift 2;;
    --plugin-repo) PLUGIN_REPO="$2"; shift 2;;
    --baseline) BASELINE_FILE="$2"; shift 2;;
    --min-delta) MIN_DELTA="$2"; shift 2;;
    --bump) BUMP="$2"; shift 2;;
    --auto-release) AUTO_RELEASE=1; shift;;
    --delivery) DELIVERY="$2"; shift 2;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) sed -n '2,40p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

log() { echo "[compare-release] $*" >&2; }
die() { echo "[compare-release] ERROR: $*" >&2; exit 1; }

[[ -n "$REPORT" && -f "$REPORT" ]] || die "--report FILE not found: ${REPORT:-<unset>}"
[[ -d "$PLUGIN_REPO/.git" ]] || die "--plugin-repo is not a git checkout: $PLUGIN_REPO"
case "$BUMP" in patch|minor|major) ;; *) die "--bump must be patch|minor|major";; esac

CODEX_MANIFEST="plugins/agentic-game-development-superpowers/.codex-plugin/plugin.json"
CLAUDE_MANIFEST="plugins/agentic-game-development-superpowers/.claude-plugin/plugin.json"
BASELINE_ABS="$PLUGIN_REPO/$BASELINE_FILE"

# --- New run's mean score + metadata ------------------------------------------
NEW_SCORE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("mean_score") or 0.0)' "$REPORT")"
NEW_COUNT="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("count") or 0)' "$REPORT")"
NEW_IMAGE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("image") or "")' "$REPORT")"
CUR_VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$PLUGIN_REPO/$CODEX_MANIFEST")"
log "new run: mean_score=$NEW_SCORE over $NEW_COUNT testcase(s); current plugin v$CUR_VERSION"

# --- Baseline (may not exist yet on first ever run) ---------------------------
if [[ -f "$BASELINE_ABS" ]]; then
  BASE_SCORE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("mean_score") or 0.0)' "$BASELINE_ABS")"
  BASE_VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("version") or "?")' "$BASELINE_ABS")"
  HAVE_BASELINE=1
  log "baseline: mean_score=$BASE_SCORE (from plugin v$BASE_VERSION)"
else
  BASE_SCORE=0.0; BASE_VERSION="(none)"; HAVE_BASELINE=0
  log "no baseline file yet at $BASELINE_FILE — will establish it (no release on first run)"
fi

DELTA="$(python3 -c "print(round(float('$NEW_SCORE') - float('$BASE_SCORE'), 6))")"
IMPROVED="$(python3 -c "print('1' if (float('$NEW_SCORE') - float('$BASE_SCORE')) > float('$MIN_DELTA') else '0')")"
log "delta = $NEW_SCORE - $BASE_SCORE = $DELTA  (min-delta $MIN_DELTA; improved=$IMPROVED)"

# --- Compute the next version (semver bump) -----------------------------------
NEXT_VERSION="$(python3 - "$CUR_VERSION" "$BUMP" <<'PY'
import sys
cur, bump = sys.argv[1], sys.argv[2]
parts = (cur.split(".") + ["0", "0", "0"])[:3]
try:
    maj, minr, pat = (int(x) for x in parts)
except ValueError:
    maj, minr, pat = 0, 1, 0
if bump == "major":   maj, minr, pat = maj + 1, 0, 0
elif bump == "minor": minr, pat = minr + 1, 0
else:                 pat += 1
print(f"{maj}.{minr}.{pat}")
PY
)"

# --- Helper: write the baseline json (in-repo source of truth) ----------------
write_baseline() {
  local version="$1" score="$2"
  python3 - "$BASELINE_ABS" "$version" "$score" "$NEW_COUNT" "$NEW_IMAGE" "$DELIVERY" "$REPORT" <<'PY'
import json, sys, os
path, version, score, count, image, delivery, report = sys.argv[1:8]
# Pull the per-testcase score map from the report for provenance/diffing.
rep = json.load(open(report))
cases = {t.get("testcase_id"): t.get("score")
         for t in rep.get("testcases", []) if t.get("testcase_id")}
doc = {
    "version": version,
    "mean_score": float(score),
    "count": int(count),
    "image": image,
    "delivery": delivery or None,
    "driver": rep.get("driver"),
    "testcases": cases,
    "note": "Benchmark baseline for the CURRENT release. Updated by "
            "AIGameDevBench/scripts/compare-and-maybe-release.sh on each improving run. "
            "Timestamps intentionally omitted for deterministic diffs.",
}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
print(path)
PY
}

# --- Helper: bump both manifests (and marketplace if it pins a version) -------
bump_manifests() {
  local version="$1"
  for m in "$CODEX_MANIFEST" "$CLAUDE_MANIFEST"; do
    python3 - "$PLUGIN_REPO/$m" "$version" <<'PY'
import json, sys
path, version = sys.argv[1], sys.argv[2]
d = json.load(open(path))
d["version"] = version
with open(path, "w", encoding="utf-8") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
PY
  done
  # marketplace.json: only rewrite if it PINS a version for our plugin.
  python3 - "$PLUGIN_REPO/.claude-plugin/marketplace.json" "$version" <<'PY'
import json, sys
path, version = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)
changed = False
for p in d.get("plugins", []):
    if p.get("name") == "agentic-game-development-superpowers" and p.get("version") is not None:
        p["version"] = version; changed = True
if changed:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2); f.write("\n")
PY
}

# =============================================================================
# Decision
# =============================================================================
if [[ "$HAVE_BASELINE" == "0" ]]; then
  # First run ever: establish the baseline at the CURRENT version, no release.
  log "establishing initial baseline at v$CUR_VERSION (mean_score=$NEW_SCORE); no release."
  if [[ "$DRY_RUN" == "1" || "$AUTO_RELEASE" == "0" ]]; then
    log "(dry-run / no --auto-release) would write $BASELINE_FILE and commit it."
    exit 0
  fi
  write_baseline "$CUR_VERSION" "$NEW_SCORE" >/dev/null
  ( cd "$PLUGIN_REPO"
    git add "$BASELINE_FILE"
    git commit -m "chore(bench): establish benchmark baseline at v$CUR_VERSION (mean_score=$NEW_SCORE)" >/dev/null
    git push origin HEAD >/dev/null 2>&1 || log "WARN: push failed (commit is local)"
  )
  log "baseline established and pushed."
  exit 0
fi

if [[ "$IMPROVED" != "1" ]]; then
  log "NOT improved (delta $DELTA <= min-delta $MIN_DELTA). No release. Baseline unchanged."
  exit 0
fi

log "IMPROVED (delta $DELTA). Target release: v$CUR_VERSION -> v$NEXT_VERSION (bump=$BUMP)."
if [[ "$AUTO_RELEASE" == "0" ]]; then
  log "(no --auto-release) would: bump manifests to v$NEXT_VERSION, refresh baseline, push main."
  exit 0
fi
if [[ "$DRY_RUN" == "1" ]]; then
  log "(--dry-run) computing changes but NOT committing/pushing."
fi

# --- Apply: bump + refresh baseline + commit + push main (auto-release) -------
bump_manifests "$NEXT_VERSION"
write_baseline "$NEXT_VERSION" "$NEW_SCORE" >/dev/null
log "wrote v$NEXT_VERSION into manifests + baseline (mean_score=$NEW_SCORE)."

if [[ "$DRY_RUN" == "1" ]]; then
  log "(--dry-run) skipping git commit/push. Review changes under $PLUGIN_REPO."
  ( cd "$PLUGIN_REPO" && git --no-pager diff --stat ) || true
  exit 0
fi

( cd "$PLUGIN_REPO"
  cur_branch="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$cur_branch" != "main" ]]; then
    log "checking out main (was on $cur_branch) to auto-release..."
    git checkout main >/dev/null 2>&1 || die "cannot checkout main"
    git pull --ff-only origin main >/dev/null 2>&1 || log "WARN: pull main failed; committing on local main"
    bump_manifests "$NEXT_VERSION"
    write_baseline "$NEXT_VERSION" "$NEW_SCORE" >/dev/null
  fi
  git add "$CODEX_MANIFEST" "$CLAUDE_MANIFEST" ".claude-plugin/marketplace.json" "$BASELINE_FILE"
  git commit -m "chore(plugin): bump to v$NEXT_VERSION (benchmark improved +$DELTA over v$BASE_VERSION)" >/dev/null
  git push origin main >/dev/null 2>&1 || die "push to main failed"
)
log "pushed v$NEXT_VERSION to main — release-on-bump.yml will publish the release."
log "DONE: released v$NEXT_VERSION (mean_score $BASE_SCORE -> $NEW_SCORE, +$DELTA)."
