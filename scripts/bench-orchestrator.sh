#!/usr/bin/env bash
# bench-orchestrator.sh — turn a GitHub webhook delivery into ONE full
# AIGameDevBench parallel benchmark run (one k8s Job per testcase, claude +
# agentic-game-development plugin as the harness).
#
# Two trigger sources, pick with --mode:
#
#   http   (default) — run a tiny HTTP endpoint on :$PORT/trigger. The modified
#                      github-webhook receiver POSTs here after a validated push:
#                        POST /trigger
#                        Header: x-bench-token: <BENCH_TRIGGER_TOKEN>
#                        Body:   {"repo":"o/r","ref":"refs/heads/main",
#                                 "after":"<sha>","delivery":"<uuid>"}
#                      See docs/webhook-forward-contract.md for the exact contract.
#
#   watch-logs      — no receiver change needed. tail the receiver pod's logs on
#                      the mc-winter-zhao cluster (winter has pods/log there) and
#                      trigger on each `accepted GitHub webhook delivery` line,
#                      de-duped by its delivery id. Robust fallback when the
#                      receiver cannot reach this host or its source is untouched.
#
# Both paths de-dupe by delivery id (persisted in $STATE_DIR/seen) so a redelivery
# or a log re-read never launches a second batch, and both invoke the SAME
# benchmark launcher: scripts/run_k8s_matrix.sh.
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
#   --auto-release      if the run beats the stored baseline, bump the plugin
#                       version + push main (release-on-bump.yml then publishes).
#                       OFF by default: without it, the compare step only reports.
#   --min-delta N       min mean-score gain to count as an improvement (MIN_DELTA, default 0)
#   --bump T            patch|minor|major bump on release       (BUMP, default patch)
#   -h                  help
#
# After each batch, scripts/compare-and-maybe-release.sh compares the run's mean
# score to $PLUGIN_REPO/workflow/benchmark-baseline.json and, on improvement with
# --auto-release, cuts a new release. Per-batch release log: results/<id>/release.log.
#
# Each triggered batch runs run_k8s_matrix.sh with --no-build/--no-push (the
# image is built once ahead of time by build_runner_image.sh), driver=command,
# and the harness Secret. Results + logs land under $RESULTS_ROOT/<delivery>/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="${MODE:-http}"
PORT="${PORT:-8899}"
IMAGE="${IMAGE:-}"
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
    -h|--help) sed -n '2,60p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

[[ -n "$IMAGE" ]] || { echo "ERROR: --image is required" >&2; exit 2; }
mkdir -p "$STATE_DIR" "$RESULTS_ROOT"
SEEN_FILE="$STATE_DIR/seen"
touch "$SEEN_FILE"

log() { echo "[orchestrator] $*" >&2; }

# id must be a safe token so it's usable in a path/label.
sanitize_id() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '-' | cut -c1-64; }

# --- Launch ONE benchmark batch for a delivery (de-duped, backgrounded) -------
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

# --- Mode: watch-logs ---------------------------------------------------------
run_watch_logs() {
  [[ -r "$WEBHOOK_KUBECONFIG" ]] \
    || { echo "ERROR: cannot read WEBHOOK_KUBECONFIG=$WEBHOOK_KUBECONFIG" >&2; exit 1; }
  log "watch-logs mode: tailing deployment/github-webhook logs in ns=$WEBHOOK_NS"
  log "  (cluster from $WEBHOOK_KUBECONFIG; de-dupe by delivery id)"
  # --tail=0 so we only react to NEW deliveries after startup. Each accepted line
  # is JSON: {"message":"accepted GitHub webhook delivery","event":"push",
  #           "delivery":"...","repository":"o/r", ...}. Extract fields with python.
  KUBECONFIG="$WEBHOOK_KUBECONFIG" kubectl -n "$WEBHOOK_NS" \
      logs -f --tail=0 deployment/github-webhook 2>/dev/null \
  | while IFS= read -r line; do
      case "$line" in *'accepted GitHub webhook delivery'*) ;; *) continue;; esac
      # Parse the JSON line safely; skip non-push events.
      eval "$(printf '%s' "$line" | python3 -c '
import sys, json, shlex
try:
    o = json.loads(sys.stdin.readline())
except Exception:
    sys.exit(0)
ev = o.get("event") or ""
if ev != "push":
    sys.exit(0)
d = o.get("delivery") or ""
r = o.get("repository") or ""
print("D=%s R=%s" % (shlex.quote(d), shlex.quote(r)))
')"
      [[ -n "${D:-}" ]] || continue
      launch_batch "$D" "${R:-}" "" ""
      unset D R
    done
}

# --- Mode: http ---------------------------------------------------------------
run_http() {
  log "http mode: listening on 0.0.0.0:$PORT  (POST /trigger)"
  [[ -n "$BENCH_TRIGGER_TOKEN" ]] || log "WARN: no --token set; /trigger is unauthenticated"
  # A minimal, dependency-free HTTP server. We export what the handler needs and
  # call back into this script's launch_batch via a fifo-free approach: the
  # python server writes a compact command line to stdout lines that the bash
  # reader turns into launch_batch calls. This keeps k8s/launch logic in bash.
  export BENCH_TRIGGER_TOKEN PORT
  # The python server validates + emits one TAB-separated line per accepted
  # trigger; the bash while-loop turns each into a launch_batch call. Process
  # substitution keeps k8s/launch logic in bash and avoids heredoc-in-pipeline
  # ordering hazards.
  while IFS= read -r cmdline; do
    # cmdline is: DELIVERY<TAB>REPO<TAB>REF<TAB>SHA  (already validated by server)
    IFS=$'\t' read -r d r ref sha <<<"$cmdline"
    launch_batch "$d" "$r" "$ref" "$sha"
  done < <(python3 - <<'PY'
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ.get("BENCH_TRIGGER_TOKEN", "")
PORT = int(os.environ.get("PORT", "8899"))

def emit(delivery, repo, ref, sha):
    # One tab-separated line to stdout -> bash launch_batch. Flush immediately.
    sys.stdout.write("\t".join([delivery, repo or "", ref or "", sha or ""]) + "\n")
    sys.stdout.flush()

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
        try:
            o = json.loads(raw or b"{}")
        except Exception:
            self._send(400, {"error": "bad json"}); return
        delivery = str(o.get("delivery") or "").strip()
        if not delivery:
            self._send(400, {"error": "missing delivery"}); return
        emit(delivery, str(o.get("repo") or ""), str(o.get("ref") or ""),
             str(o.get("after") or o.get("sha") or ""))
        # Accept fast; the benchmark runs asynchronously on the bash side.
        self._send(202, {"accepted": True, "delivery": delivery})

HTTPServer(("0.0.0.0", PORT), H).serve_forever()
PY
  )
}

case "$MODE" in
  http)       run_http ;;
  watch-logs) run_watch_logs ;;
  *) echo "ERROR: --mode must be http or watch-logs, got: $MODE" >&2; exit 2;;
esac
