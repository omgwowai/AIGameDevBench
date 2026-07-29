#!/usr/bin/env bash
# bench-orchestrator.sh — turn a GitHub `pull_request` (action=opened) webhook
# delivery into ONE full AIGameDevBench parallel benchmark run of the PR head
# (one k8s Job per testcase, claude + agentic-game-development plugin as the
# harness), gated behind a strict "beats historical best" check.
#
# ONLY a freshly-OPENED pull request triggers a run. synchronize / reopened /
# ready_for_review, and all `push` events, are intentionally ignored.
#
# Two trigger sources, pick with --mode:
#
#   http   (default) — run a tiny HTTP endpoint on :$PORT/trigger. The
#                      github-webhook receiver POSTs here after a validated,
#                      HMAC-checked pull_request delivery:
#                        POST /trigger
#                        Header: x-bench-token: <BENCH_TRIGGER_TOKEN>
#                        Body:   {"delivery":"<uuid>","repo":"o/r",
#                                 "event":"pull_request","action":"opened",
#                                 "pr_number":"42","head_sha":"<sha>",
#                                 "base_ref":"main"}
#                      See docs/webhook-forward-contract.md for the exact contract.
#
#   watch-logs      — no receiver change needed. tail the receiver pod's logs on
#                      the mc-winter-zhao cluster (winter has pods/log there) and
#                      trigger on each `accepted GitHub webhook delivery` line
#                      that is a pull_request opened, de-duped by head.sha. Robust
#                      fallback when the receiver cannot reach this host.
#
# Both paths de-dupe (http by delivery id, PR path by head.sha; persisted in
# $STATE_DIR/seen) so a redelivery or log re-read never launches a second run,
# and both dispatch a PR opened to the SAME gated candidate flow:
# scripts/bench-candidate.sh.
#
# Usage:
#   scripts/bench-orchestrator.sh --mode http        [options]
#   scripts/bench-orchestrator.sh --mode watch-logs  [options]
#
# Options (env in parens):
#   --mode M            http | watch-logs                    (MODE, default http)
#   --port N            http mode listen port                (PORT, default 8899)
#   --image IMAGE       runner image ref (built by build_runner_image.sh)
#                                                            (IMAGE, required)
#   --secret NAME       k8s Secret w/ HARNESS_CMD+ANTHROPIC_API_KEY
#                                                            (HARNESS_SECRET, default aigdbench-harness)
#   --jobs N            max concurrent Jobs                  (JOBS, default 16)
#   --testcases-dir D   testcases dir INSIDE the image       (TESTCASES_DIR, default /app/testcases_filtered)
#   --namespace NS      k8s ns for the Jobs                  (NAMESPACE, default default)
#   --results-root DIR  per-delivery results land here       (RESULTS_ROOT, default ./results)
#   --state-dir DIR     seen-delivery + pid state            (STATE_DIR, default ./.orchestrator)
#   --token TOK         shared secret required on /trigger   (BENCH_TRIGGER_TOKEN; http mode)
#   --webhook-ns NS     receiver namespace (watch-logs)      (WEBHOOK_NS, default webhook)
#   --webhook-kubeconfig F  kubeconfig that can read the receiver's logs
#                                                            (WEBHOOK_KUBECONFIG, default ~/mc-winter-zhao-kubeconfig)
#   --no-build          pass through: reuse pushed image, skip build/push in matrix
#   --plugin-repo DIR   agentic-game-development checkout for baseline compare/release
#                                                            (PLUGIN_REPO, default ../agentic-game-development)
#   --rebuild           per trigger: git-pull the plugin repo and rebuild the runner
#                       image so the LATEST plugin is benchmarked; publishes by
#                       pushing to the registry (Job's imagePullPolicy: Always then
#                       pulls the fresh :latest) (REBUILD=1)
#   --rebuild-import-k3s like --rebuild but loads the image into local k3s containerd
#                       instead of pushing (single-node k3s w/o registry push)
#   --plugin-base-ref R diff base for "this run's plugin changes" recorded in the
#                       report (PLUGIN_BASE_REF, default origin/main)
#   --auto-release      if the run is NOT LOWER than the stored baseline, bump the
#                       plugin version + push main (release-on-bump.yml publishes).
#                       OFF by default: without it, the compare step only reports.
#   --min-delta N       min mean-score delta to count as release-worthy; the gate is
#                       delta >= min-delta, so the default 0 means "not lower than
#                       baseline releases" (MIN_DELTA, default 0)
#   --bump T            patch|minor|major bump on release       (BUMP, default patch)
#   -h                  help
#
# Per trigger (with --rebuild): pull plugin -> rebuild+import runner image ->
# fan out one Job per testcase -> aggregate report -> embed the plugin change
# (commit + diff) and trigger info into report.json -> compare-and-maybe-release.sh
# compares the run's mean score to $PLUGIN_REPO/workflow/benchmark-baseline.json
# and, if not lower (delta >= min-delta) with --auto-release, cuts a new release.
# Per-batch logs: results/<id>/{batch,rebuild,release}.log, plugin_change.json.
#
# Each triggered batch runs run_k8s_matrix.sh with --no-build/--no-push (the
# image is built once ahead of time by build_runner_image.sh), driver=command,
# and the harness Secret. Results + logs land under $RESULTS_ROOT/<delivery>/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="${MODE:-http}"
PORT="${PORT:-8899}"
# Received webhooks are appended to $WEBHOOK_LOG (see below) and shown in the
# AIGameDevBench dashboard's Webhooks tab (aigdbench serve --webhook-log ...).
# There is no separate viewer process anymore.
# Default to the public beaver_hub-public runner image (pullable with the
# beaver_hub-public robot creds, and built with the claude CLI baked in). The
# old xiaojun_private image is NOT pullable here → ImagePullBackOff → every
# testcase fails. Override with --image / IMAGE only with a known-pullable ref.
IMAGE="${IMAGE:-harbor.omgwow.ai/beaver_hub-public/aigdbench-runner:latest}"
HARNESS_SECRET="${HARNESS_SECRET:-aigdbench-harness}"
JOBS="${JOBS:-16}"
TESTCASES_DIR="${TESTCASES_DIR:-/app/testcases_filtered}"
NAMESPACE="${NAMESPACE:-default}"
# Local testcase dir used ONLY to enumerate ids (passed to the matrix via -t) so
# the orchestrator never needs docker at runtime. The image's in-container path
# ($TESTCASES_DIR) must mirror this dir's basenames. Empty => let the matrix
# auto-discover from the image (requires docker on the matrix host).
LOCAL_TESTCASES_DIR="${LOCAL_TESTCASES_DIR:-$REPO_ROOT/testcases_filtered}"
RESULTS_ROOT="${RESULTS_ROOT:-$REPO_ROOT/results}"
STATE_DIR="${STATE_DIR:-$REPO_ROOT/.orchestrator}"
BENCH_TRIGGER_TOKEN="${BENCH_TRIGGER_TOKEN:-}"
WEBHOOK_NS="${WEBHOOK_NS:-webhook}"
WEBHOOK_KUBECONFIG="${WEBHOOK_KUBECONFIG:-$HOME/mc-winter-zhao-kubeconfig}"
NO_BUILD_FLAG="--no-build"   # image is prebuilt; matrix should not rebuild by default
# Post-benchmark: compare mean score vs the plugin's stored baseline and, if the
# run improved, auto-release a new agentic-game-development version.
PLUGIN_REPO="${PLUGIN_REPO:-$REPO_ROOT/../agentic-game-development}"
AUTO_RELEASE=0          # off unless --auto-release given (safety)
MIN_DELTA="${MIN_DELTA:-0}"
BUMP="${BUMP:-patch}"
# Per-trigger rebuild: pull the plugin repo and rebuild+import the runner image so
# the LATEST plugin is what gets benchmarked. On => matrix reuses the freshly
# imported local image (implies --no-build in the matrix). Off => reuse $IMAGE as-is.
REBUILD=0
REBUILD_IMPORT_K3S="${REBUILD_IMPORT_K3S:-0}"  # 0 => --rebuild pushes to registry; 1 => import into local k3s instead
PLUGIN_BASE_REF="${PLUGIN_BASE_REF:-origin/main}"  # diff base for "this run's plugin changes"
# --- v2: PR-triggered gated candidate flow (see docs/spec-v2.md) --------------
# A `pull_request` (action=opened) webhook is dispatched to
# scripts/bench-candidate.sh, which benchmarks the PR head in isolation and
# (with --auto-release) merges+releases only if it strictly beats the historical
# best. All other events (push, other PR actions) are ignored. IMAGE_REPO is the
# repo WITHOUT tag; the candidate appends an immutable :<sha> tag itself.
IMAGE_REPO="${IMAGE_REPO:-harbor.omgwow.ai/beaver_hub-public/aigdbench-runner}"
CANDIDATE_ENGINE="${CANDIDATE_ENGINE:-auto}"   # docker|podman|auto for candidate image build
# Reuse AUTO_RELEASE/MIN_DELTA/BUMP (declared above) for the candidate gate too.

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --image) IMAGE="$2"; shift 2;;
    --secret) HARNESS_SECRET="$2"; shift 2;;
    --jobs) JOBS="$2"; shift 2;;
    --testcases-dir) TESTCASES_DIR="$2"; shift 2;;
    --local-testcases-dir) LOCAL_TESTCASES_DIR="$2"; shift 2;;
    --namespace) NAMESPACE="$2"; shift 2;;
    --results-root) RESULTS_ROOT="$2"; shift 2;;
    --state-dir) STATE_DIR="$2"; shift 2;;
    --token) BENCH_TRIGGER_TOKEN="$2"; shift 2;;
    --webhook-ns) WEBHOOK_NS="$2"; shift 2;;
    --webhook-kubeconfig) WEBHOOK_KUBECONFIG="$2"; shift 2;;
    --no-build) NO_BUILD_FLAG="--no-build"; shift;;
    --build) NO_BUILD_FLAG=""; shift;;
    --plugin-repo) PLUGIN_REPO="$2"; shift 2;;
    --auto-release) AUTO_RELEASE=1; shift;;
    --min-delta) MIN_DELTA="$2"; shift 2;;
    --bump) BUMP="$2"; shift 2;;
    --rebuild) REBUILD=1; shift;;
    --rebuild-import-k3s) REBUILD=1; REBUILD_IMPORT_K3S=1; shift;;
    --plugin-base-ref) PLUGIN_BASE_REF="$2"; shift 2;;
    --image-repo) IMAGE_REPO="$2"; shift 2;;
    --candidate-engine) CANDIDATE_ENGINE="$2"; shift 2;;
    -h|--help) sed -n '2,60p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

[[ -n "$IMAGE" ]] || { echo "ERROR: --image is required" >&2; exit 2; }
mkdir -p "$STATE_DIR" "$RESULTS_ROOT"
SEEN_FILE="$STATE_DIR/seen"
touch "$SEEN_FILE"
# JSONL log of every webhook received (both http and watch-logs paths append
# here). The AIGameDevBench dashboard shows it in its Webhooks tab; start the
# dashboard with:  aigdbench serve --webhook-log "$STATE_DIR/webhooks.jsonl"
WEBHOOK_LOG="$STATE_DIR/webhooks.jsonl"
touch "$WEBHOOK_LOG"

log() { echo "[orchestrator] $*" >&2; }

# id must be a safe token so it's usable in a path/label.
sanitize_id() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '-' | cut -c1-64; }

# Capture the plugin repo's git state + THIS run's plugin changes (vs base ref)
# into a JSON file. Embedded into report.json so the report records exactly what
# plugin content produced the scores. Safe if the base ref is unknown (falls back
# to last-commit only). Truncates the diff so a huge changeset can't bloat report.
PLUGIN_SKILL_SUBDIR="plugins/agentic-game-development-superpowers"
capture_plugin_change() {
  local outfile="$1"
  local pr="$PLUGIN_REPO" base="$PLUGIN_BASE_REF"
  [[ -d "$pr/.git" ]] || { echo '{}' > "$outfile"; return 1; }
  local commit branch subject files difftext logtext base_sha
  commit="$(git -C "$pr" rev-parse HEAD 2>/dev/null || echo '')"
  branch="$(git -C "$pr" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
  subject="$(git -C "$pr" log -1 --pretty=%s 2>/dev/null || echo '')"
  base_sha="$(git -C "$pr" rev-parse "$base" 2>/dev/null || echo '')"
  local range
  if [[ -n "$base_sha" && "$base_sha" != "$commit" ]]; then
    range="$base_sha..HEAD"
  else
    range="HEAD~1..HEAD"   # fall back to just the last commit
  fi
  files="$(git -C "$pr" diff --name-only "$range" -- "$PLUGIN_SKILL_SUBDIR" 2>/dev/null || true)"
  logtext="$(git -C "$pr" log --pretty='%h %s' "$range" -- "$PLUGIN_SKILL_SUBDIR" 2>/dev/null | head -50 || true)"
  # Cap the diff at ~4000 lines so report.json stays sane.
  difftext="$(git -C "$pr" diff "$range" -- "$PLUGIN_SKILL_SUBDIR" 2>/dev/null | head -4000 || true)"
  PC_COMMIT="$commit" PC_BRANCH="$branch" PC_SUBJECT="$subject" \
  PC_BASE="$base" PC_BASE_SHA="$base_sha" PC_RANGE="$range" \
  PC_FILES="$files" PC_LOG="$logtext" PC_DIFF="$difftext" \
  python3 - "$outfile" <<'PY'
import json, os, sys
out = sys.argv[1]
files = [f for f in (os.environ.get("PC_FILES") or "").splitlines() if f.strip()]
doc = {
    "repo": "agentic-game-development",
    "commit": os.environ.get("PC_COMMIT") or None,
    "branch": os.environ.get("PC_BRANCH") or None,
    "subject": os.environ.get("PC_SUBJECT") or None,
    "base_ref": os.environ.get("PC_BASE") or None,
    "base_sha": os.environ.get("PC_BASE_SHA") or None,
    "range": os.environ.get("PC_RANGE") or None,
    "changed_files": files,
    "log": os.environ.get("PC_LOG") or "",
    "diff": os.environ.get("PC_DIFF") or "",
    "diff_truncated": len((os.environ.get("PC_DIFF") or "").splitlines()) >= 4000,
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2)
PY
}

# --- Launch ONE benchmark batch for a delivery (de-duped, backgrounded) -------
# LEGACY / UNREACHABLE: this was the v1 push-triggered post-hoc path. Since the
# orchestrator now only acts on `pull_request` (action=opened) -> launch_candidate,
# nothing dispatches here anymore. Kept for reference and possible manual reuse.
launch_batch() {
  local delivery="$1" repo="${2:-}" ref="${3:-}" sha="${4:-}"
  local id; id="$(sanitize_id "$delivery")"
  [[ -n "$id" ]] || { log "empty delivery id; skipping"; return 0; }

  # De-dupe: atomically claim this delivery id.
  if grep -qxF "$id" "$SEEN_FILE" 2>/dev/null; then
    log "delivery $id already processed; skipping"
    return 0
  fi
  echo "$id" >> "$SEEN_FILE"

  local out="$RESULTS_ROOT/$id"
  mkdir -p "$out"
  log "TRIGGER delivery=$id repo=$repo ref=$ref sha=$sha -> $out"
  # Metadata breadcrumb. NOTE: run_k8s_matrix.sh wipes $out/*.json on startup, so
  # the breadcrumb must NOT be a .json in $out — write it as trigger.meta.
  cat > "$out/trigger.meta" <<EOF
{"delivery":"$delivery","repo":"$repo","ref":"$ref","after":"$sha","image":"$IMAGE"}
EOF

  # --- Per-trigger: pull the plugin repo + rebuild the runner image ------------
  # So the LATEST agentic-game-development plugin is what this run benchmarks.
  # build_runner_image.sh does the git pull + vendors the plugin + builds and
  # publishes the image. Registry path (default): --push to Harbor, and the Job's
  # imagePullPolicy: Always pulls the fresh :latest. Local-k3s path: --import-k3s
  # loads it into containerd instead. We capture the plugin's git state (commit +
  # the diff of THIS run's plugin changes vs $PLUGIN_BASE_REF) for report.json.
  # .meta (not .json) so run_k8s_matrix.sh's `rm -f $out/*.json` doesn't wipe it.
  local plugin_change_file="$out/plugin_change.meta"
  if [[ "$REBUILD" == "1" ]]; then
    local publish_flag="--push"
    [[ "$REBUILD_IMPORT_K3S" == "1" ]] && publish_flag="--import-k3s"
    log "delivery=$id: pulling plugin + rebuilding runner image $IMAGE ($publish_flag)..."
    if "$REPO_ROOT/scripts/build_runner_image.sh" -i "$IMAGE" "$publish_flag" \
         -P "$PLUGIN_REPO" > "$out/rebuild.log" 2>&1; then
      log "delivery=$id: runner image rebuilt + published ($publish_flag)"
    else
      log "delivery=$id: REBUILD FAILED (see $out/rebuild.log); using existing image"
    fi
  fi
  # Capture plugin change metadata (works whether or not we rebuilt). NOTE: it
  # must NOT be a *.json in $out — run_k8s_matrix.sh wipes $out/*.json on startup.
  # Use .meta; the injection step below reads it back after the matrix.
  capture_plugin_change "$plugin_change_file" || true

  # Enumerate testcase ids locally (docker-free) so the orchestrator host needs no
  # docker; pass them explicitly via -t. The image's $TESTCASES_DIR must contain
  # the same basenames. Fall back to matrix auto-discovery if the local dir is
  # unset/empty (that path DOES require docker on the matrix host).
  local tc_args=()
  if [[ -n "$LOCAL_TESTCASES_DIR" && -d "$LOCAL_TESTCASES_DIR" ]]; then
    local tcs
    tcs="$(find "$LOCAL_TESTCASES_DIR" -mindepth 1 -maxdepth 1 -type d \
             -printf '%f\n' 2>/dev/null | grep -vE '^(_|README)' | sort | tr '\n' ' ')"
    [[ -n "$tcs" ]] && tc_args=(-t "$tcs")
    log "enumerated $(echo $tcs | wc -w) testcase(s) from $LOCAL_TESTCASES_DIR"
  fi

  # Fan out: one k8s Job per testcase, gated at JOBS, claude harness via Secret.
  # run_k8s_matrix.sh reads HARNESS_CMD + ANTHROPIC_API_KEY from the Secret (-s).
  (
    "$REPO_ROOT/scripts/run_k8s_matrix.sh" \
      -i "$IMAGE" \
      -d command \
      -s "$HARNESS_SECRET" \
      -j "$JOBS" \
      -n "$NAMESPACE" \
      -D "$TESTCASES_DIR" \
      "${tc_args[@]}" \
      -o "$out" \
      --no-push $NO_BUILD_FLAG \
      > "$out/batch.log" 2>&1
    ec=$?
    echo "EXIT=$ec" >> "$out/batch.log"
    log "batch delivery=$id finished exit=$ec (report: $out/report.md)"

    # --- Embed this run's plugin changes into report.json --------------------
    # The report must record exactly what plugin content produced the scores:
    # the trigger delivery + the plugin git commit and the diff of this run's
    # changes (captured pre-matrix into plugin_change.meta, which survives the
    # matrix's $out/*.json wipe). Also surface it as the documented artifact
    # plugin_change.json now that the matrix is done wiping.
    [[ -f "$plugin_change_file" ]] && cp "$plugin_change_file" "$out/plugin_change.json"
    if [[ -f "$out/report.json" && -f "$plugin_change_file" ]]; then
      python3 - "$out/report.json" "$plugin_change_file" "$delivery" "$repo" "$ref" "$sha" <<'PY'
import json, sys
report_path, pc_path, delivery, repo, ref, sha = sys.argv[1:7]
rep = json.load(open(report_path))
try:
    pc = json.load(open(pc_path))
except Exception:
    pc = {}
rep["trigger"] = {"delivery": delivery, "repo": repo, "ref": ref, "after": sha}
rep["plugin_change"] = pc
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(rep, f, indent=2)
PY
      log "batch delivery=$id: embedded plugin_change into report.json"
    fi

    # --- Post-benchmark: compare vs plugin baseline, maybe auto-release -------
    # Only when the batch produced a report. Non-improving runs are a no-op; an
    # improving run bumps the plugin version and pushes main (release-on-bump.yml
    # then publishes). --auto-release must be set for any write to happen.
    if [[ -f "$out/report.json" ]]; then
      local rel_flags=(--report "$out/report.json" --plugin-repo "$PLUGIN_REPO"
                       --min-delta "$MIN_DELTA" --bump "$BUMP" --delivery "$id")
      [[ "$AUTO_RELEASE" == "1" ]] && rel_flags+=(--auto-release)
      "$REPO_ROOT/scripts/compare-and-maybe-release.sh" "${rel_flags[@]}" \
        >> "$out/release.log" 2>&1 \
        && log "batch delivery=$id compare/release step done (see $out/release.log)" \
        || log "batch delivery=$id compare/release step FAILED (see $out/release.log)"
    else
      log "batch delivery=$id: no report.json; skipping compare/release"
    fi
  ) &
  log "batch delivery=$id dispatched (pid=$!)"
}

# --- Launch a PR-triggered gated CANDIDATE (v2, see docs/spec-v2.md) ----------
# pull_request events go here: benchmark the PR head in isolation on an immutable
# candidate image and (with --auto-release) merge+release only if it strictly
# beats the historical best. De-duped by head.sha (a PR's synchronize re-fires
# on every push, but the same head only needs one run).
launch_candidate() {
  local delivery="$1" repo="$2" pr="$3" head_sha="$4" base_ref="${5:-main}"
  [[ -n "$head_sha" ]] || { log "PR event without head_sha; skipping"; return 0; }
  [[ -n "$repo" ]] || { log "PR event without repo; skipping"; return 0; }
  # De-dupe by head.sha (not delivery): synchronize re-delivers the same head.
  local key="pr-${pr}-${head_sha}"
  if grep -qxF "$key" "$SEEN_FILE" 2>/dev/null; then
    log "candidate $key already processed; skipping"
    return 0
  fi
  echo "$key" >> "$SEEN_FILE"

  local cand_flags=(--commit "$head_sha" --pr "$pr" --repo "$repo"
                    --delivery "$delivery" --image-repo "$IMAGE_REPO"
                    --secret "$HARNESS_SECRET" --jobs "$JOBS"
                    --testcases-dir "$TESTCASES_DIR"
                    --local-testcases-dir "$LOCAL_TESTCASES_DIR"
                    --namespace "$NAMESPACE" --results-root "$RESULTS_ROOT"
                    --plugin-repo "$PLUGIN_REPO" --bump "$BUMP"
                    --engine "$CANDIDATE_ENGINE")
  [[ "$AUTO_RELEASE" == "1" ]] && cand_flags+=(--auto-release)
  log "CANDIDATE PR #$pr head=$head_sha repo=$repo -> bench-candidate.sh"
  ( "$REPO_ROOT/scripts/bench-candidate.sh" "${cand_flags[@]}" ) &
  log "candidate PR #$pr dispatched (pid=$!)"
}

# --- Mode: watch-logs ---------------------------------------------------------
run_watch_logs() {
  [[ -r "$WEBHOOK_KUBECONFIG" ]] \
    || { echo "ERROR: cannot read WEBHOOK_KUBECONFIG=$WEBHOOK_KUBECONFIG" >&2; exit 1; }
  log "watch-logs mode: tailing deployment/github-webhook logs in ns=$WEBHOOK_NS"
  log "  (cluster from $WEBHOOK_KUBECONFIG; de-dupe by delivery id)"
  log "  webhooks logged to $WEBHOOK_LOG (view in dashboard: serve --webhook-log ...)"
  # --tail=0 so we only react to NEW deliveries after startup. Each accepted line
  # is JSON: {"message":"accepted GitHub webhook delivery","event":"push",
  #           "delivery":"...","repository":"o/r", ...}. Extract fields with python.
  KUBECONFIG="$WEBHOOK_KUBECONFIG" kubectl -n "$WEBHOOK_NS" \
      logs -f --tail=0 deployment/github-webhook 2>/dev/null \
  | while IFS= read -r line; do
      case "$line" in *'accepted GitHub webhook delivery'*) ;; *) continue;; esac
      # Parse the JSON line safely; record EVERY delivery to the webhook log for
      # the viewer, then dispatch only freshly-OPENED pull requests.
      eval "$(printf '%s' "$line" | WEBHOOK_LOG="$WEBHOOK_LOG" python3 -c '
import sys, json, shlex, os, time
try:
    o = json.loads(sys.stdin.readline())
except Exception:
    sys.exit(0)
ev = (o.get("event") or "").lower()
d = o.get("delivery") or ""
r = o.get("repository") or ""
pr = o.get("pull_request") or {}
prnum = str(o.get("pr_number") or pr.get("number") or "")
head = str(o.get("head_sha") or (pr.get("head") or {}).get("sha") or "")
base = str(o.get("base_ref") or (pr.get("base") or {}).get("ref") or "main")
action = (o.get("action") or "").lower()
is_pr = ev in ("pull_request","pr") or bool(prnum and head)
# Only a freshly-OPENED pull request triggers a benchmark. Everything else
# (synchronize/reopened/ready_for_review, and all push events) is ignored.
decision, reason = "skipped", (ev or "non-pull_request")
if is_pr and (not action or action == "opened") and prnum and head:
    decision, reason = "accepted", ""
elif is_pr:
    decision, reason = "skipped", (action or "missing pr_number/head_sha")
log = os.environ.get("WEBHOOK_LOG", "")
if log:
    rec = {"time": time.time(), "source": "watch-logs", "decision": decision,
           "skipped_reason": reason, "delivery": d,
           "event": ev or ("pull_request" if is_pr else "?"), "action": action,
           "repo": r, "pr_number": prnum, "head_sha": head, "base_ref": base,
           "body": o}
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
if decision == "accepted":
    print("EV=pr D=%s R=%s PR=%s HEAD=%s BASE=%s" % tuple(
        shlex.quote(x) for x in (d, r, prnum, head, base)))
')"
      case "${EV:-}" in
        pr)   [[ -n "${HEAD:-}" ]] && launch_candidate "$D" "${R:-}" "$PR" "$HEAD" "${BASE:-main}" ;;
      esac
      unset EV D R PR HEAD BASE
    done
}

# --- Mode: http ---------------------------------------------------------------
run_http() {
  log "http mode: listening on 0.0.0.0:$PORT  (POST /trigger)"
  [[ -n "$BENCH_TRIGGER_TOKEN" ]] || log "WARN: no --token set; /trigger is unauthenticated"
  log "  webhooks logged to $WEBHOOK_LOG (view in dashboard: serve --webhook-log ...)"
  # A minimal, dependency-free HTTP server. We export what the handler needs and
  # call back into this script's launch_batch via a fifo-free approach: the
  # python server writes a compact command line to stdout lines that the bash
  # reader turns into launch_batch calls. This keeps k8s/launch logic in bash.
  export BENCH_TRIGGER_TOKEN PORT WEBHOOK_LOG
  # The python server validates + emits one TAB-separated line per accepted
  # trigger; the bash while-loop turns each into a launch_batch call. Process
  # substitution keeps k8s/launch logic in bash and avoids heredoc-in-pipeline
  # ordering hazards.
  while IFS= read -r cmdline; do
    # cmdline is \x1f-separated (unit separator), first column is the event:
    #   pull_request  EV=pr  \x1f d \x1f repo \x1f \x1f \x1f prnum \x1f head_sha \x1f base_ref
    # \x1f (not tab) so the empty ref/sha fields don't collapse under IFS.
    IFS=$'\x1f' read -r ev d r ref sha pr head_sha base_ref <<<"$cmdline"
    case "$ev" in
      pr)   launch_candidate "$d" "$r" "$pr" "$head_sha" "${base_ref:-main}" ;;
    esac
  done < <(python3 - <<'PY'
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ.get("BENCH_TRIGGER_TOKEN", "")
PORT = int(os.environ.get("PORT", "8899"))
WEBHOOK_LOG = os.environ.get("WEBHOOK_LOG", "")

def emit(ev, delivery, repo, ref, sha, pr="", head_sha="", base_ref=""):
    # One \x1f (unit separator) delimited line to stdout -> bash dispatch. NOT
    # tab: tab is IFS-whitespace so consecutive empty fields (ref+sha are empty
    # for PR events) would collapse and shift head_sha/base_ref. \x1f never
    # appears in webhook fields and is not IFS-whitespace, so empties survive.
    sys.stdout.write("\x1f".join([ev, delivery, repo or "", ref or "", sha or "",
                                  pr or "", head_sha or "", base_ref or ""]) + "\n")
    sys.stdout.flush()

def record(rec):
    # Append one JSON line to the webhook log so the viewer can render it.
    if not WEBHOOK_LOG:
        return
    rec.setdefault("time", time.time())
    rec.setdefault("source", "http")
    try:
        with open(WEBHOOK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass

class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):  # quiet; bash side logs triggers
        pass
    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})
    def do_POST(self):
        if self.path != "/trigger":
            self._send(404, {"error": "not found"}); return
        if TOKEN:
            if self.headers.get("x-bench-token", "") != TOKEN:
                self._send(401, {"error": "bad token"}); return
        n = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(n) if n > 0 else b"{}"
        # Print the full received webhook request to stderr (stdout is reserved
        # for the TAB-separated dispatch line consumed by the bash reader).
        sys.stderr.write(
            "[orchestrator] === incoming POST %s from %s ===\n"
            % (self.path, self.client_address[0])
        )
        sys.stderr.write(
            "[orchestrator] headers:\n%s\n" % "".join(
                "[orchestrator]   %s: %s\n" % (k, v) for k, v in self.headers.items()
            )
        )
        try:
            pretty = json.dumps(json.loads(raw or b"{}"), indent=2, ensure_ascii=False)
        except Exception:
            pretty = (raw or b"").decode("utf-8", "replace")
        sys.stderr.write("[orchestrator] body:\n%s\n" % pretty)
        sys.stderr.flush()
        client = self.client_address[0]
        try:
            o = json.loads(raw or b"{}")
        except Exception:
            record({"decision": "error", "error": "bad json", "client": client,
                    "event": "?", "body": (raw or b"").decode("utf-8", "replace")})
            self._send(400, {"error": "bad json"}); return
        delivery = str(o.get("delivery") or "").strip()
        if not delivery:
            record({"decision": "error", "error": "missing delivery",
                    "client": client, "event": str(o.get("event") or "?"), "body": o})
            self._send(400, {"error": "missing delivery"}); return
        # Event type: explicit "event" field, else infer from payload shape.
        # A pull_request delivery carries pr_number/head_sha (see spec-v2 contract);
        # some receivers nest it under o["pull_request"].
        event = str(o.get("event") or "").strip().lower()
        pr = o.get("pull_request") or {}
        pr_number = str(o.get("pr_number") or o.get("pr") or pr.get("number") or "").strip()
        head_sha = str(o.get("head_sha") or (pr.get("head") or {}).get("sha") or "").strip()
        base_ref = str(o.get("base_ref") or (pr.get("base") or {}).get("ref") or "main").strip()
        action = str(o.get("action") or "").strip().lower()
        is_pr = event in ("pull_request", "pr") or bool(pr_number and head_sha)
        base = {"delivery": delivery, "client": client,
                "event": event or ("pull_request" if is_pr else "?"),
                "action": action, "repo": str(o.get("repo") or ""),
                "pr_number": pr_number, "head_sha": head_sha,
                "base_ref": base_ref, "body": o}
        if is_pr:
            # Only a freshly-OPENED PR is a benchmark trigger. synchronize /
            # reopened / ready_for_review are intentionally ignored.
            if action and action != "opened":
                record({**base, "decision": "skipped", "skipped_reason": action})
                self._send(202, {"accepted": False, "skipped": action}); return
            if not (pr_number and head_sha):
                record({**base, "decision": "error",
                        "error": "missing pr_number/head_sha"})
                self._send(400, {"error": "pull_request missing pr_number/head_sha"}); return
            record({**base, "decision": "accepted"})
            emit("pr", delivery, str(o.get("repo") or ""), "", "", pr_number, head_sha, base_ref)
            self._send(202, {"accepted": True, "event": "pull_request", "pr": pr_number})
            return
        # Non-PR events (push, etc.) no longer trigger a benchmark.
        record({**base, "decision": "skipped",
                "skipped_reason": event or "non-pull_request"})
        self._send(202, {"accepted": False, "skipped": event or "non-pull_request"})

HTTPServer(("0.0.0.0", PORT), H).serve_forever()
PY
  )
}

case "$MODE" in
  http)       run_http ;;
  watch-logs) run_watch_logs ;;
  *) echo "ERROR: --mode must be http or watch-logs, got: $MODE" >&2; exit 2;;
esac
