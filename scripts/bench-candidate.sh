#!/usr/bin/env bash
# bench-candidate.sh — candidate-branch, gated benchmark → merge → release.
#
# Difference from the legacy "post-hoc" flow (bench-orchestrator.sh +
# compare-and-maybe-release.sh): there, the commit is ALREADY on main and the
# benchmark only validates it "not lower than baseline". Here, a webhook commit
# is quarantined on a throwaway candidate branch, benchmarked in isolation, and
# only MERGED INTO main + RELEASED if it is STRICTLY BETTER than main's historical
# best score. main becomes a benchmark-guarded protected branch.
#
# Per webhook (repo, ref, commit sha, delivery):
#   1. candidate branch:  git fetch origin main; git checkout -B bench-<sha>
#      origin/main; git cherry-pick <sha>   (conflict => abort, record, no merge)
#   2. temp version:      <main-version>-bench.<sha8>  (prerelease semver + source
#      marker written into plugin.json)
#   3. candidate image:   vendor bench-<sha> plugin -> build -> push image:<sha>
#      (immutable tag; no mutable :latest stale-cache hazard)
#   4. parallel benchmark: one k8s Job per testcase (claude + candidate plugin)
#      -> aggregate report.json (records trigger + plugin_change + mean_score)
#   5. gate:              mean_score > main's HISTORICAL BEST (best_score in the
#      baseline). Not better => discard candidate, keep report, exit 0.
#   6. merge to main:     git checkout main; merge bench-<sha>
#   7. repackage on main: proper version (drop -bench suffix, semver bump)
#   8. release:           push main -> release-on-bump.yml publishes; release
#      notes carry SOURCE (commit/author/subject) + TEST DATA (mean, per-case,
#      delta vs best, image digest).
#
# Guard rails: writes (merge/version bump/push) happen ONLY with --auto-release.
# Without it, everything up to the gate runs and the decision is reported.
#
# Usage:
#   scripts/bench-candidate.sh --commit <sha> [--repo o/r] [--delivery id] \
#     --image-repo harbor.omgwow.ai/beaver_hub-public/aigdbench-runner \
#     [--auto-release] [--bump patch|minor|major] [--dry-run]
#
# Options (env in parens):
#   --commit SHA       plugin commit to evaluate (COMMIT, required)
#   --repo O/R         source repo (for provenance)                 (REPO)
#   --delivery ID      webhook delivery id (dedupe/provenance)       (DELIVERY)
#   --plugin-repo DIR  agentic-game-development checkout   (PLUGIN_REPO, default ../agentic-game-development)
#   --image-repo REF   image repo WITHOUT tag; candidate tag :<sha> is appended
#                                     (IMAGE_REPO, default harbor.omgwow.ai/beaver_hub-public/aigdbench-runner)
#   --secret NAME      harness k8s Secret                 (HARNESS_SECRET, default aigdbench-harness)
#   --jobs N           max concurrent Jobs                (JOBS, default 16)
#   --timeout SEC      per-testcase harness timeout        (TIMEOUT, default 1800)
#   --testcases-dir D  in-image testcases dir             (TESTCASES_DIR, default /app/testcases_filtered)
#   --local-testcases-dir D  local dir to enumerate ids   (LOCAL_TESTCASES_DIR, default ./testcases_filtered)
#   --namespace NS     k8s namespace                      (NAMESPACE, default default)
#   --results-root DIR results land under here            (RESULTS_ROOT, default ./results)
#   --bump T           semver bump on release             (BUMP, default patch)
#   --auto-release     actually merge to main + bump + push (else gate report only)
#   --dry-run          do everything except git push/merge writes
#   --engine E         docker | podman (ENGINE, auto-detected)
#   -h                 help
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMMIT="${COMMIT:-}"
REPO="${REPO:-}"
DELIVERY="${DELIVERY:-}"
PR_NUMBER="${PR_NUMBER:-}"   # when set, this is a PR-triggered candidate (v2 flow)
PLUGIN_REPO="${PLUGIN_REPO:-$REPO_ROOT/../agentic-game-development}"
IMAGE_REPO="${IMAGE_REPO:-harbor.omgwow.ai/beaver_hub-public/aigdbench-runner}"
HARNESS_SECRET="${HARNESS_SECRET:-aigdbench-harness}"
JOBS="${JOBS:-16}"
TIMEOUT="${TIMEOUT:-1800}"
TESTCASES_DIR="${TESTCASES_DIR:-/app/testcases_filtered}"
LOCAL_TESTCASES_DIR="${LOCAL_TESTCASES_DIR:-$REPO_ROOT/testcases_filtered}"
NAMESPACE="${NAMESPACE:-default}"
RESULTS_ROOT="${RESULTS_ROOT:-$REPO_ROOT/results}"
BUMP="${BUMP:-patch}"
AUTO_RELEASE=0
DRY_RUN="${DRY_RUN:-0}"
ENGINE="${ENGINE:-auto}"
BASELINE_FILE="workflow/benchmark-baseline.json"
PLUGIN_SUBDIR="plugins/agentic-game-development-superpowers"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --commit) COMMIT="$2"; shift 2;;
    --pr) PR_NUMBER="$2"; shift 2;;
    --repo) REPO="$2"; shift 2;;
    --delivery) DELIVERY="$2"; shift 2;;
    --plugin-repo) PLUGIN_REPO="$2"; shift 2;;
    --image-repo) IMAGE_REPO="$2"; shift 2;;
    --secret) HARNESS_SECRET="$2"; shift 2;;
    --jobs) JOBS="$2"; shift 2;;
    --timeout) TIMEOUT="$2"; shift 2;;
    --testcases-dir) TESTCASES_DIR="$2"; shift 2;;
    --local-testcases-dir) LOCAL_TESTCASES_DIR="$2"; shift 2;;
    --namespace) NAMESPACE="$2"; shift 2;;
    --results-root) RESULTS_ROOT="$2"; shift 2;;
    --bump) BUMP="$2"; shift 2;;
    --auto-release) AUTO_RELEASE=1; shift;;
    --dry-run) DRY_RUN=1; shift;;
    --engine) ENGINE="$2"; shift 2;;
    -h|--help) sed -n '2,60p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

log() { echo "[candidate] $*" >&2; }
die() { echo "[candidate] ERROR: $*" >&2; exit 1; }

[[ -n "$COMMIT" ]] || die "--commit <sha> is required"
[[ -d "$PLUGIN_REPO/.git" ]] || die "--plugin-repo is not a git checkout: $PLUGIN_REPO"
case "$BUMP" in patch|minor|major) ;; *) die "--bump must be patch|minor|major";; esac

# --- engine (docker|podman) ---------------------------------------------------
if [[ "$ENGINE" == "auto" ]]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then ENGINE=docker
  elif command -v podman >/dev/null 2>&1; then ENGINE=podman
  else die "no working docker or podman"; fi
fi
ENGINE_ARGS=()
if [[ "$ENGINE" == "podman" ]]; then
  PODMAN_ROOT="${PODMAN_ROOT:-/tmp/podman-store-$USER}"
  ENGINE_ARGS=(--root "$PODMAN_ROOT" --storage-driver "${PODMAN_DRIVER:-overlay}")
fi
ENG() { "$ENGINE" "${ENGINE_ARGS[@]}" "$@"; }
log "engine=$ENGINE ${ENGINE_ARGS[*]:-}"

SHA8="${COMMIT:0:8}"
CAND_BRANCH="bench-$SHA8"
IMAGE="$IMAGE_REPO:$SHA8"                       # immutable per-candidate tag
ID="cand-${DELIVERY:-$SHA8}"
OUT="$RESULTS_ROOT/$(printf '%s' "$ID" | tr -c 'A-Za-z0-9._-' '-' | cut -c1-64)"
mkdir -p "$OUT" "$OUT/logs"
CODEX_MANIFEST="$PLUGIN_SUBDIR/.codex-plugin/plugin.json"
CLAUDE_MANIFEST="$PLUGIN_SUBDIR/.claude-plugin/plugin.json"
BASELINE_ABS="$PLUGIN_REPO/$BASELINE_FILE"

cat > "$OUT/trigger.meta" <<EOF
{"delivery":"$DELIVERY","repo":"$REPO","commit":"$COMMIT","pr":"${PR_NUMBER:-}","candidate_branch":"$CAND_BRANCH","image":"$IMAGE"}
EOF
log "candidate: commit=$SHA8 branch=$CAND_BRANCH image=$IMAGE out=$OUT"

# --- 1. Build candidate branch: origin/main + this commit --------------------
log "[1] building candidate branch $CAND_BRANCH from origin/main + $SHA8"
git -C "$PLUGIN_REPO" fetch origin main --quiet || die "fetch origin main failed"
# The PR head commit lives on a PR branch, not on main, so `fetch origin main`
# alone does NOT bring the object into the local repo. Without it `cherry-pick`
# fails with "bad object" -- which used to be mis-reported as a conflict and the
# benchmark silently skipped. Fetch the commit explicitly (best-effort; GitHub
# serves reachable SHAs), then verify the object is present before proceeding.
git -C "$PLUGIN_REPO" fetch origin "$COMMIT" --quiet 2>/dev/null || true
# Remember where the plugin repo was so we can restore it afterward.
ORIG_REF="$(git -C "$PLUGIN_REPO" rev-parse --abbrev-ref HEAD)"
restore_repo() {
  # Abort any in-progress cherry-pick, discard the uncommitted -bench marker
  # edits (else `checkout` refuses), leave the candidate branch, and delete it.
  # Idempotent + safe on every exit path: if we already merged and moved to
  # main, switching back to ORIG_REF and dropping the temp branches is still ok.
  git -C "$PLUGIN_REPO" cherry-pick --abort >/dev/null 2>&1 || true
  git -C "$PLUGIN_REPO" reset --hard --quiet >/dev/null 2>&1 || true
  git -C "$PLUGIN_REPO" checkout --quiet "$ORIG_REF" 2>/dev/null || true
  git -C "$PLUGIN_REPO" branch -D "$CAND_BRANCH" >/dev/null 2>&1 || true
  # Also drop the local release-bump branch if one was created (BUMP_BRANCH is
  # set only in the release path; guard against unset under `set -u`).
  [[ -n "${BUMP_BRANCH:-}" ]] && \
    git -C "$PLUGIN_REPO" branch -D "$BUMP_BRANCH" >/dev/null 2>&1 || true
}
# Always leave the plugin repo clean, however we exit (success, die, or kill).
# Without this a failed/interrupted candidate stranded the repo on bench-<sha>
# with dirty manifests, breaking the next run's checkout.
trap restore_repo EXIT
# If the commit object still isn't available, this is NOT a conflict -- the head
# was never pushed/reachable (deleted branch, force-push, private fork, etc.).
# Record a distinct status so the dashboard shows the real reason.
if ! git -C "$PLUGIN_REPO" cat-file -e "${COMMIT}^{commit}" 2>/dev/null; then
  log "[1] COMMIT UNAVAILABLE: $SHA8 could not be fetched from origin (not on any reachable ref?)"
  echo '{"status":"commit_unavailable","commit":"'"$COMMIT"'","note":"PR head commit not fetchable from origin (fetch origin <sha> failed); no benchmark run"}' > "$OUT/candidate.json"
  exit 0
fi
git -C "$PLUGIN_REPO" checkout -B "$CAND_BRANCH" origin/main --quiet || die "cannot create $CAND_BRANCH"
MAIN_SHA="$(git -C "$PLUGIN_REPO" rev-parse origin/main)"
if git -C "$PLUGIN_REPO" merge-base --is-ancestor "$COMMIT" origin/main 2>/dev/null; then
  log "commit $SHA8 already in origin/main — evaluating main as-is (no cherry-pick)"
else
  # The commit object exists (verified above), so a non-zero cherry-pick here is
  # a GENUINE merge conflict against origin/main -- distinct from "bad object".
  if ! git -C "$PLUGIN_REPO" cherry-pick "$COMMIT" >/dev/null 2>&1; then
    git -C "$PLUGIN_REPO" cherry-pick --abort >/dev/null 2>&1 || true
    log "[1] CHERRY-PICK CONFLICT: $SHA8 does not apply cleanly onto origin/main"
    echo '{"status":"conflict","commit":"'"$COMMIT"'","note":"cherry-pick onto origin/main hit a real merge conflict"}' > "$OUT/candidate.json"
    restore_repo
    exit 0
  fi
fi
CAND_SHA="$(git -C "$PLUGIN_REPO" rev-parse HEAD)"
AUTHOR="$(git -C "$PLUGIN_REPO" log -1 --pretty='%an' "$COMMIT" 2>/dev/null || echo '')"
SUBJECT="$(git -C "$PLUGIN_REPO" log -1 --pretty='%s' "$COMMIT" 2>/dev/null || echo '')"

# --- 2. Temp (prerelease) version + source marker ----------------------------
BASE_VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$PLUGIN_REPO/$CODEX_MANIFEST")"
# Strip any existing prerelease suffix from origin/main's version before tagging.
BASE_VERSION="${BASE_VERSION%%-*}"
TEMP_VERSION="${BASE_VERSION}-bench.${SHA8}"
log "[2] temp version $TEMP_VERSION (base $BASE_VERSION); marking source in manifests"
for m in "$CODEX_MANIFEST" "$CLAUDE_MANIFEST"; do
  python3 - "$PLUGIN_REPO/$m" "$TEMP_VERSION" "$COMMIT" "$REPO" "$DELIVERY" <<'PY'
import json, sys
path, ver, commit, repo, delivery = sys.argv[1:6]
d = json.load(open(path))
d["version"] = ver
d["_bench_source"] = {"commit": commit, "repo": repo or None, "delivery": delivery or None}
json.dump(d, open(path, "w"), indent=2); open(path, "a").write("\n")
PY
done

# --- 3. Candidate image (immutable :sha) -------------------------------------
log "[3] building candidate image $IMAGE (plugin from $CAND_BRANCH)"
if DOCKER="$ENGINE ${ENGINE_ARGS[*]}" "$REPO_ROOT/scripts/build_runner_image.sh" \
     -i "$IMAGE" --push -P "$PLUGIN_REPO" --no-pull > "$OUT/build.log" 2>&1; then
  log "[3] candidate image built + pushed: $IMAGE"
else
  log "[3] BUILD FAILED (see $OUT/build.log)"
  restore_repo
  die "candidate image build failed"
fi

# Capture the exact plugin content benchmarked (commit + diff vs origin/main).
git -C "$PLUGIN_REPO" diff "origin/main..$CAND_BRANCH" -- "$PLUGIN_SUBDIR" 2>/dev/null | head -4000 > "$OUT/plugin.diff" || true

# Restore the plugin repo working state now that the image has the content baked.
restore_repo

# --- 4. Parallel benchmark on the candidate image ----------------------------
log "[4] benchmarking candidate image across testcases"
tc_args=()
if [[ -n "$LOCAL_TESTCASES_DIR" && -d "$LOCAL_TESTCASES_DIR" ]]; then
  tcs="$(find "$LOCAL_TESTCASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null \
         | grep -vE '^(_|README)' | sort | tr '\n' ' ')"
  [[ -n "$tcs" ]] && tc_args=(-t "$tcs")
fi
"$REPO_ROOT/scripts/run_k8s_matrix.sh" \
  -i "$IMAGE" -d command -s "$HARNESS_SECRET" \
  -j "$JOBS" -n "$NAMESPACE" -T "$TIMEOUT" \
  -D "$TESTCASES_DIR" "${tc_args[@]}" \
  -o "$OUT" --no-push --no-build > "$OUT/batch.log" 2>&1 || log "matrix exited nonzero (see batch.log)"

[[ -f "$OUT/report.json" ]] || die "no report.json produced (see $OUT/batch.log)"
NEW_SCORE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("mean_score") or 0.0)' "$OUT/report.json")"

# Embed trigger + candidate provenance into the report.
python3 - "$OUT/report.json" "$DELIVERY" "$REPO" "$COMMIT" "$CAND_BRANCH" "$AUTHOR" "$SUBJECT" "${PR_NUMBER:-}" <<'PY'
import json, sys
p, delivery, repo, commit, branch, author, subject, pr = sys.argv[1:9]
r = json.load(open(p))
r["trigger"] = {"delivery": delivery, "repo": repo, "commit": commit, "pr": pr or None,
                "candidate_branch": branch, "author": author, "subject": subject}
json.dump(r, open(p, "w"), indent=2)
PY

# --- 5. Gate: strictly better than main's HISTORICAL BEST --------------------
BEST_SCORE=0.0; BEST_VERSION="(none)"
if [[ -f "$BASELINE_ABS" ]]; then
  BEST_SCORE="$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("best_score", d.get("mean_score") or 0.0))' "$BASELINE_ABS")"
  BEST_VERSION="$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("best_version") or d.get("version") or "?")' "$BASELINE_ABS")"
fi
BETTER="$(python3 -c "print('1' if float('$NEW_SCORE') > float('$BEST_SCORE') else '0')")"
log "[5] gate: candidate mean=$NEW_SCORE vs historical best=$BEST_SCORE (from $BEST_VERSION) -> better=$BETTER"

cat > "$OUT/candidate.json" <<EOF
{"status":"benchmarked","commit":"$COMMIT","candidate_branch":"$CAND_BRANCH",
 "image":"$IMAGE","mean_score":$NEW_SCORE,"historical_best":$BEST_SCORE,
 "better":$([ "$BETTER" = 1 ] && echo true || echo false),
 "author":"$AUTHOR","subject":"$SUBJECT"}
EOF

if [[ "$BETTER" != "1" ]]; then
  log "[5] NOT better than best ($NEW_SCORE <= $BEST_SCORE). Discarding candidate; no merge/release."
  log "    report: $OUT/report.json ; candidate: $OUT/candidate.json"
  exit 0
fi

log "[5] BETTER: $NEW_SCORE > $BEST_SCORE. Candidate qualifies for merge + release."
if [[ "$AUTO_RELEASE" == "0" ]]; then
  log "(no --auto-release) would: merge $CAND_BRANCH -> main, bump version, push, publish release."
  exit 0
fi

# --- 6/7/8. Merge to main, repackage, release --------------------------------
NEXT_VERSION="$(python3 - "$BASE_VERSION" "$BUMP" <<'PY'
import sys
cur, bump = sys.argv[1], sys.argv[2]
maj, minr, pat = (int(x) for x in (cur.split(".")+["0","0","0"])[:3])
if bump=="major": maj,minr,pat = maj+1,0,0
elif bump=="minor": minr,pat = minr+1,0
else: pat += 1
print(f"{maj}.{minr}.{pat}")
PY
)"
log "[6/7/8] merge $SHA8 into main, repackage as v$NEXT_VERSION, release"
if [[ "$DRY_RUN" == "1" ]]; then
  log "(--dry-run) would merge + bump to v$NEXT_VERSION + push main. Stopping before writes."
  exit 0
fi

write_release_baseline() {  # version score
  python3 - "$BASELINE_ABS" "$1" "$2" "$BEST_SCORE" "$OUT/report.json" "$COMMIT" "$IMAGE" "$DELIVERY" <<'PY'
import json, sys, os
path, version, score, prev_best, report, commit, image, delivery = sys.argv[1:9]
rep = json.load(open(report))
cases = {t.get("testcase_id"): t.get("score") for t in rep.get("testcases", []) if t.get("testcase_id")}
score = float(score)
doc = {
    "version": version, "mean_score": score,
    "best_score": max(score, float(prev_best)), "best_version": version,
    "count": rep.get("count"), "image": image, "delivery": delivery or None,
    "plugin_commit": commit, "driver": rep.get("driver"), "testcases": cases,
    "note": "CURRENT release baseline; best_score = historical high-water mark. "
            "Updated by bench-candidate.sh only when a candidate STRICTLY beats best_score.",
}
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(doc, open(path, "w"), indent=2); open(path, "a").write("\n")
PY
}

# Concurrency guard (spec v2): main may have advanced (another PR merged) while
# this candidate ran, raising best_score. Re-read it and re-check strict-better
# before mutating main.
git -C "$PLUGIN_REPO" fetch origin main --quiet || true
if [[ -f "$BASELINE_ABS" ]]; then
  RECHECK_BEST="$(git -C "$PLUGIN_REPO" show "origin/main:$BASELINE_FILE" 2>/dev/null \
    | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("best_score", d.get("mean_score") or 0.0))' 2>/dev/null || echo "$BEST_SCORE")"
  if [[ "$(python3 -c "print('1' if float('$NEW_SCORE') > float('$RECHECK_BEST') else '0')")" != "1" ]]; then
    log "[6] main advanced: best is now $RECHECK_BEST >= candidate $NEW_SCORE. Not merging; request rebase+retest."
    [[ -n "$PR_NUMBER" ]] && gh pr comment "$PR_NUMBER" --repo "$REPO" \
      --body "候选未合并：main 已前进，当前历史最高 $RECHECK_BEST ≥ 本候选 $NEW_SCORE。请 rebase 后重测。" 2>/dev/null || true
    exit 0
  fi
fi

# Step 2 wrote the temporary -bench version + _bench_source into the manifests as
# UNCOMMITTED working-tree edits on the candidate branch. They were only needed
# to build the candidate image; leaving them dirty makes the upcoming
# `git checkout main` fail ("local changes would be overwritten"). Discard them
# now — step 7 rewrites the real version on main anyway.
git -C "$PLUGIN_REPO" checkout -- "$CODEX_MANIFEST" "$CLAUDE_MANIFEST" 2>/dev/null || true
git -C "$PLUGIN_REPO" reset --hard --quiet 2>/dev/null || true

if [[ -n "$PR_NUMBER" ]]; then
  # v2: the commit lives in a PR — merge the PR itself (squash) so the merge is
  # recorded on GitHub with PR provenance, rather than cherry-picking locally.
  log "[6] merging PR #$PR_NUMBER (squash) into main"
  gh pr merge "$PR_NUMBER" --repo "$REPO" --squash --admin \
    --subject "plugin: merge PR #$PR_NUMBER (benchmark $NEW_SCORE > best $BEST_SCORE)" \
    --body "Auto-merged by bench-candidate: mean_score=$NEW_SCORE > historical best $BEST_SCORE (from $BEST_VERSION). Source ${REPO:-?}@${SHA8}." \
    || die "gh pr merge #$PR_NUMBER failed"
  ( cd "$PLUGIN_REPO"; git checkout main --quiet; git pull --ff-only origin main --quiet ) || die "sync main after merge failed"
else
  # push-triggered fallback: cherry-pick the commit onto main directly.
  ( cd "$PLUGIN_REPO"
    git checkout main --quiet
    git pull --ff-only origin main --quiet || log "WARN: pull main failed; using local main"
    if ! git merge-base --is-ancestor "$COMMIT" HEAD 2>/dev/null; then
      git cherry-pick "$COMMIT" --quiet || die "cherry-pick $COMMIT onto main failed at merge stage"
    fi
  )
fi
# Repackage on main: real semver, drop the -bench suffix, refresh baseline + best.
# NOTE: `main` is protected by a ruleset requiring changes via PR (direct
# `git push origin main` is rejected: GH013 "Changes must be made through a pull
# request"). So the version bump goes through its OWN short-lived PR + admin
# squash-merge, mirroring the candidate merge above, instead of a direct push.
BUMP_BRANCH="release-v${NEXT_VERSION}-${SHA8}"
( cd "$PLUGIN_REPO"
  # Start the bump branch from the just-merged main so it includes the PR.
  git checkout -B "$BUMP_BRANCH" main --quiet
  for m in "$CODEX_MANIFEST" "$CLAUDE_MANIFEST"; do
    python3 -c "import json,sys;d=json.load(open(sys.argv[1]));d.pop('_bench_source',None);d['version']=sys.argv[2];json.dump(d,open(sys.argv[1],'w'),indent=2);open(sys.argv[1],'a').write('\n')" "$m" "$NEXT_VERSION"
  done
)
write_release_baseline "$NEXT_VERSION" "$NEW_SCORE"
( cd "$PLUGIN_REPO"
  git add "$CODEX_MANIFEST" "$CLAUDE_MANIFEST" "$BASELINE_FILE"
  git commit --quiet -m "chore(plugin): release v$NEXT_VERSION (benchmark $NEW_SCORE > best $BEST_SCORE)

Source: ${REPO:-?}@${SHA8} — ${SUBJECT:-}
Author: ${AUTHOR:-?}
Benchmark: mean_score=$NEW_SCORE over candidate image $IMAGE
Previous historical best: $BEST_SCORE (from $BEST_VERSION)"
  # Push the BRANCH (allowed by the ruleset) then merge it via PR (--admin so no
  # human review is required for this automated bump).
  git push -u origin "$BUMP_BRANCH" --quiet || die "push bump branch failed"
) || die "prepare bump branch failed"
BUMP_PR_URL="$(gh pr create --repo "$REPO" --base main --head "$BUMP_BRANCH" \
  --title "chore(plugin): release v$NEXT_VERSION (benchmark $NEW_SCORE)" \
  --body "Automated version bump + baseline by bench-candidate after merging source PR #${PR_NUMBER:-?} (mean_score=$NEW_SCORE > best $BEST_SCORE)." \
  2>/dev/null)" || die "create bump PR failed"
log "[7] bump PR: $BUMP_PR_URL"
gh pr merge "$BUMP_PR_URL" --repo "$REPO" --squash --admin --delete-branch \
  || die "merge bump PR failed"
# Sync local main to the merged bump so subsequent runs read the new baseline.
( cd "$PLUGIN_REPO"; git checkout main --quiet; git pull --ff-only origin main --quiet ) \
  || log "WARN: could not fast-forward local main after bump merge"
log "[8] bump v$NEXT_VERSION merged to main via PR — release-on-bump.yml will publish."
log "DONE: released v$NEXT_VERSION (mean_score $NEW_SCORE > best $BEST_SCORE); source ${SHA8}."
