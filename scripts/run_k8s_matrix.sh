#!/usr/bin/env bash
# run_k8s_matrix.sh — build the AIGameDevBench image from source, push it to a
# registry, then fan out ONE Kubernetes Job per testcase across the cluster.
# Each Job runs a single testcase and prints its per-testcase report to the pod
# log (wrapped in AIGDBENCH_REPORT markers). The orchestrator scrapes each Job's
# log into ./results/<id>.json, then aggregates everything into a combined
# report.json + report.md.
#
# This is the cluster analogue of the local docker matrix script: instead of
# `docker run` per testcase we `kubectl create -f <job>` per testcase, gate the
# number of in-flight Jobs, and collect results from logs (no shared PVC needed).
#
# Usage:
#   scripts/run_k8s_matrix.sh -i registry.example.com/aigdbench:latest [options]
#
# Options (also settable via env):
#   -i IMAGE        FULL image ref incl. registry            (IMAGE, required to push)
#   -j N            max concurrent Jobs in flight            (JOBS, default: 16)
#   -d DRIVER       noop | patch | command                  (DRIVER, default: command)
#   -c "CMD {task}" harness command for DRIVER=command       (HARNESS_CMD)
#   -t "id id ..."  explicit testcase ids                    (default: all discovered)
#   -n NAMESPACE    k8s namespace                            (NAMESPACE, default: default)
#   -D DIR          testcases dir INSIDE the image           (TESTCASES_DIR, default: /app/testcases)
#   -o DIR          results/output dir on this host          (OUT_DIR, default: ./results)
#   -g VERSION      Godot version build-arg                  (GODOT_VERSION, default: 4.5-stable)
#   -T SECONDS      per-testcase harness timeout             (TIMEOUT, default: 900)
#   -A SECONDS      Job activeDeadlineSeconds (hard kill)    (ACTIVE_DEADLINE, default: TIMEOUT+300)
#   -s SECRET       k8s Secret with HARNESS_CMD + API keys   (HARNESS_SECRET)
#   -H CMD          build-arg to install a harness in-image  (HARNESS_INSTALL)
#   --cpu-req V --mem-req V --cpu-lim V --mem-lim V           (pod resources)
#   --no-build      skip docker build (reuse pushed image)
#   --no-push       skip docker push (image already in registry / local node)
#   --keep-jobs     don't delete Jobs after collecting (for debugging)
#   -h              help
#
# Requirements on this host: docker (build/push), kubectl (configured for the
# target cluster), envsubst (gettext), python3.
#
# Examples:
#   # Golden-patch smoke over the whole matrix (should all score 1.00):
#   scripts/run_k8s_matrix.sh -i reg/aigdbench:latest -d patch
#
#   # Real AI harness, secret carries HARNESS_CMD + ANTHROPIC_API_KEY:
#   kubectl create secret generic aigdbench-harness \
#     --from-literal=HARNESS_CMD='claude -p {task} --dangerously-skip-permissions' \
#     --from-literal=ANTHROPIC_API_KEY=sk-...
#   scripts/run_k8s_matrix.sh -i reg/aigdbench:latest -d command \
#     -H 'npm i -g @anthropic-ai/claude-code' -s aigdbench-harness -j 20
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IMAGE="${IMAGE:-}"
JOBS="${JOBS:-16}"
DRIVER="${DRIVER:-command}"
HARNESS_CMD="${HARNESS_CMD:-}"
NAMESPACE="${NAMESPACE:-default}"
TESTCASES_DIR="${TESTCASES_DIR:-/app/testcases}"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/results}"
GODOT_VERSION="${GODOT_VERSION:-4.5-stable}"
TIMEOUT="${TIMEOUT:-900}"
ACTIVE_DEADLINE="${ACTIVE_DEADLINE:-}"
# TTL for finished Jobs. Generous by default (4h) so early-finishing Jobs aren't
# GC'd before the collector (which waits in launch order) scrapes their logs.
TTL_AFTER_FINISHED="${TTL_AFTER_FINISHED:-14400}"
HARNESS_SECRET="${HARNESS_SECRET:-}"
HARNESS_INSTALL="${HARNESS_INSTALL:-}"
# CPU request is a SCHEDULING RESERVATION, not real usage. The harness is
# I/O-bound (waits on the AI API + Godot), using ~6% CPU in practice, so a large
# request needlessly serializes Jobs on a small node (a 2-core node fits only ~3
# Jobs at 500m -> the rest sit Pending). 250m lets ~2x more run concurrently
# while the 2-core limit still caps real CPU bursts. Raise via --cpu-req if a
# testcase is genuinely CPU-bound.
CPU_REQ="${CPU_REQ:-250m}"; MEM_REQ="${MEM_REQ:-1Gi}"
CPU_LIM="${CPU_LIM:-2}";    MEM_LIM="${MEM_LIM:-4Gi}"
TESTCASES=""
DO_BUILD=1; DO_PUSH=1; KEEP_JOBS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i) IMAGE="$2"; shift 2;;
    -j) JOBS="$2"; shift 2;;
    -d) DRIVER="$2"; shift 2;;
    -c) HARNESS_CMD="$2"; shift 2;;
    -t) TESTCASES="$2"; shift 2;;
    -n) NAMESPACE="$2"; shift 2;;
    -D) TESTCASES_DIR="$2"; shift 2;;
    -o) OUT_DIR="$2"; shift 2;;
    -g) GODOT_VERSION="$2"; shift 2;;
    -T) TIMEOUT="$2"; shift 2;;
    -A) ACTIVE_DEADLINE="$2"; shift 2;;
    -s) HARNESS_SECRET="$2"; shift 2;;
    -H) HARNESS_INSTALL="$2"; shift 2;;
    --cpu-req) CPU_REQ="$2"; shift 2;;
    --mem-req) MEM_REQ="$2"; shift 2;;
    --cpu-lim) CPU_LIM="$2"; shift 2;;
    --mem-lim) MEM_LIM="$2"; shift 2;;
    --no-build) DO_BUILD=0; shift;;
    --no-push) DO_PUSH=0; shift;;
    --keep-jobs) KEEP_JOBS=1; shift;;
    -h|--help) sed -n '2,60p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

# --- Preflight ---------------------------------------------------------------
for bin in docker kubectl envsubst python3; do
  command -v "$bin" >/dev/null 2>&1 || { echo "ERROR: '$bin' not found on PATH" >&2; exit 1; }
done
[[ -n "$IMAGE" ]] || { echo "ERROR: -i IMAGE (full registry ref) is required" >&2; exit 2; }
kubectl get ns "$NAMESPACE" >/dev/null 2>&1 || {
  echo "ERROR: namespace '$NAMESPACE' not reachable (is kubectl configured?)" >&2; exit 1; }
if [[ "$DRIVER" == "command" && -z "$HARNESS_CMD" && -z "$HARNESS_SECRET" ]]; then
  echo "ERROR: -d command needs a harness command: pass -c 'CMD {task}' or a -s SECRET carrying HARNESS_CMD" >&2
  exit 2
fi
: "${ACTIVE_DEADLINE:=$((TIMEOUT + 300))}"

# A run id ties this batch's Jobs together for labelling/cleanup. No Date.now in
# the shell here is fine — use a PID + namespace stamp (deterministic enough).
RUN_ID="r$$"
mkdir -p "$OUT_DIR" "$OUT_DIR/logs"
rm -f "$OUT_DIR"/*.json 2>/dev/null || true

# --- 0. Ensure on-demand project snapshots exist BEFORE the build ------------
# git-derived filtered cases (survey-history_*) reference shared snapshots that
# are NOT committed; the image COPYs testcases_filtered/, so materialise them on
# the host first (one-time network clone; idempotent). See scripts/make_snapshots.py.
if [[ "$DO_BUILD" == "1" ]] && ls testcases_filtered/*/snapshot.json >/dev/null 2>&1; then
  echo ">>> Ensuring project snapshots for git-derived filtered cases..."
  python3 "$REPO_ROOT/scripts/make_snapshots.py" --testcases-dir testcases_filtered
fi

# --- 1. Build + push image ---------------------------------------------------
if [[ "$DO_BUILD" == "1" ]]; then
  echo ">>> Building $IMAGE from source (Godot $GODOT_VERSION)..."
  docker build \
    --build-arg "GODOT_VERSION=$GODOT_VERSION" \
    ${HARNESS_INSTALL:+--build-arg "HARNESS_INSTALL=$HARNESS_INSTALL"} \
    -f docker/Dockerfile \
    -t "$IMAGE" .
else
  echo ">>> Skipping build, reusing $IMAGE"
fi
if [[ "$DO_PUSH" == "1" ]]; then
  echo ">>> Pushing $IMAGE to registry..."
  docker push "$IMAGE"
else
  echo ">>> Skipping push"
fi

# --- 2. Discover testcases ---------------------------------------------------
if [[ -z "$TESTCASES" ]]; then
  echo ">>> Discovering testcases from image ($TESTCASES_DIR)..."
  TESTCASES="$(docker run --rm --entrypoint aigdbench "$IMAGE" \
               list --testcases-dir "$TESTCASES_DIR" | awk 'NF{print $1}')"
fi
# shellcheck disable=SC2206
TC_ARR=($TESTCASES)
[[ "${#TC_ARR[@]}" -gt 0 ]] || { echo "ERROR: no testcases to run" >&2; exit 1; }
echo ">>> ${#TC_ARR[@]} testcase(s), driver=$DRIVER, concurrency=$JOBS, ns=$NAMESPACE"

# --- 3. envFrom / harness plumbing -------------------------------------------
# If a Secret is named it supplies HARNESS_CMD + provider keys via envFrom.
# An explicit -c (HARNESS_CMD) is injected as an inline env entry in the pod
# template BEFORE create -- k8s Job pod templates are immutable after creation,
# so post-create `kubectl set env` is invalid ("field is immutable"). An inline
# env entry also takes precedence over the Secret's envFrom, so -c overrides the
# command while the Secret still provides provider API keys.
if [[ -n "$HARNESS_SECRET" ]]; then
  ENV_FROM='[{"secretRef":{"name":"'"$HARNESS_SECRET"'"}}]'
else
  ENV_FROM='[]'
fi
# Render the optional HARNESS_CMD env entry (empty string when no -c given).
# json.dumps handles YAML-safe quoting of arbitrary command text (quotes,
# braces, backslashes). Indentation matches the template's env list (12 spaces).
if [[ "$DRIVER" == "command" && -n "$HARNESS_CMD" ]]; then
  HARNESS_CMD_YAML="$(HC="$HARNESS_CMD" python3 -c '
import json, os
v = json.dumps(os.environ["HC"])
print("            - name: HARNESS_CMD")
print("              value: " + v)
')"
else
  HARNESS_CMD_YAML=""
fi
export IMAGE DRIVER TIMEOUT GODOT_BINARY="godot" TESTCASES_DIR NAMESPACE
export ACTIVE_DEADLINE TTL_AFTER_FINISHED CPU_REQ MEM_REQ CPU_LIM MEM_LIM ENV_FROM RUN_ID
export HARNESS_CMD_YAML
HARNESS_ID="$DRIVER"; export HARNESS_ID

sanitize() { echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | cut -c1-40; }

render_job() {
  # $1 = testcase id ; writes rendered manifest to stdout. HARNESS_CMD (if any)
  # is baked in via ${HARNESS_CMD_YAML} before create -- pod templates are
  # immutable, so it must be present at create time, not patched afterwards.
  local tc="$1" tcl; tcl="$(sanitize "$tc")"
  TESTCASE="$tc" TESTCASE_LABEL="$tcl" \
  JOB_NAME="aigdbench-${RUN_ID}-${tcl}" \
    envsubst < docker/job-template.yaml
}

# --- 4. Fan out: one Job per testcase, gated at $JOBS ------------------------
declare -A JOBNAME=()
launch() {
  local tc="$1" tcl jn manifest
  tcl="$(sanitize "$tc")"; jn="aigdbench-${RUN_ID}-${tcl}"
  JOBNAME["$tc"]="$jn"
  manifest="$OUT_DIR/logs/${tcl}.job.yaml"
  render_job "$tc" > "$manifest"
  # HARNESS_CMD (if -c given) is already baked into the manifest via
  # ${HARNESS_CMD_YAML}; nothing to patch after create (templates are immutable).
  kubectl -n "$NAMESPACE" create -f "$manifest" >/dev/null
}

# Count Jobs of THIS run that are neither complete nor failed (i.e. in flight).
in_flight() {
  kubectl -n "$NAMESPACE" get jobs -l "aigdbench/run=$RUN_ID" \
    -o jsonpath='{range .items[*]}{.status.active}{"\n"}{end}' 2>/dev/null \
    | awk '{s+=$1} END{print s+0}'
}

echo ">>> launching Jobs (gated at $JOBS in flight)..."
for tc in "${TC_ARR[@]}"; do
  while [[ "$(in_flight)" -ge "$JOBS" ]]; do sleep 2; done
  echo ">>> launch $tc"
  launch "$tc"
done

# --- 5+6. Wait AND collect per Job, together --------------------------------
# CRITICAL: collect each Job's log the moment it finishes, NOT after waiting for
# the whole batch. Jobs carry ttlSecondsAfterFinished, so a Job that completed
# early would be garbage-collected (pod + logs gone) before a deferred "collect
# everything at the end" pass runs — yielding empty logs and bogus 0 scores.
# Waiting and collecting in the same loop keeps collection inside the TTL window.
echo ">>> waiting for and collecting ${#JOBNAME[@]} Job(s) as they finish..."
deadline_wait=$((ACTIVE_DEADLINE + 120))
extract_report() {
  # stdin: full pod log. stdout: the JSON between the markers (first match).
  awk '
    /<<<AIGDBENCH_REPORT_BEGIN/ {grab=1; next}
    /<<<AIGDBENCH_REPORT_END/   {grab=0}
    grab {print}
  '
}
collect_one() {
  local tc="$1" jn="$2" tcl raw rep st
  tcl="$(sanitize "$tc")"
  raw="$OUT_DIR/logs/${tcl}.pod.log"
  kubectl -n "$NAMESPACE" logs "job/$jn" --tail=-1 > "$raw" 2>/dev/null || true
  rep="$OUT_DIR/${tcl}.json"
  if extract_report < "$raw" | python3 -c 'import sys,json;json.load(sys.stdin)' 2>/dev/null; then
    extract_report < "$raw" > "$rep"
  else
    # No parseable report in the log — synthesize an error record so the
    # aggregate has no silent hole.
    st="$(kubectl -n "$NAMESPACE" get "job/$jn" \
          -o jsonpath='{.status.conditions[0].type}' 2>/dev/null || echo Unknown)"
    python3 - "$tc" "$st" > "$rep" <<'PY'
import json, sys
tc, st = sys.argv[1], sys.argv[2]
print(json.dumps({"harness":"k8s","count":1,"mean_score":0.0,"testcases":[
  {"testcase_id":tc,"score":0.0,"l0_l1_pass":False,
   "verifier_result":{"status":"error","error":f"no report in pod log (job condition={st})"}}]}))
PY
    echo "    !! ${tc}: no report in log (job=$st) — see $raw"
  fi
}
# Wait for a job to reach ANY terminal condition (Complete OR Failed), then
# return. Do NOT use `kubectl wait --for=condition=complete` alone: it blocks the
# full timeout for a job that FAILED (the Complete condition never arrives), so a
# single DeadlineExceeded job would stall collection ~47 min before the fallback
# runs. Poll both conditions cheaply so a failed job is detected immediately.
wait_terminal() {
  local jn="$1" waited=0 cond
  while [[ "$waited" -lt "$deadline_wait" ]]; do
    cond="$(kubectl -n "$NAMESPACE" get "job/$jn" \
      -o jsonpath='{range .status.conditions[*]}{.type}={.status} {end}' 2>/dev/null)"
    case "$cond" in
      *Complete=True*|*Failed=True*|*FailureTarget=True*) return 0 ;;
    esac
    sleep 5; waited=$((waited + 5))
  done
  return 1   # timed out; collect_one will synthesize an error record
}
for tc in "${TC_ARR[@]}"; do
  jn="${JOBNAME[$tc]}"
  wait_terminal "$jn"
  collect_one "$tc" "$jn"
done

# --- 7. Aggregate ------------------------------------------------------------
echo ">>> aggregating..."
python3 "$REPO_ROOT/scripts/aggregate_report.py" \
  --results-dir "$OUT_DIR" \
  --driver "$DRIVER" \
  --image "$IMAGE" \
  --harness-cmd "$HARNESS_CMD" \
  --namespace "$NAMESPACE" \
  --json "$OUT_DIR/report.json" \
  --markdown "$OUT_DIR/report.md"

# --- 8. Cleanup Jobs ---------------------------------------------------------
if [[ "$KEEP_JOBS" == "0" ]]; then
  echo ">>> deleting Jobs for run $RUN_ID..."
  kubectl -n "$NAMESPACE" delete jobs -l "aigdbench/run=$RUN_ID" --wait=false >/dev/null 2>&1 || true
else
  echo ">>> --keep-jobs: leaving Jobs (label aigdbench/run=$RUN_ID) for inspection"
fi

echo ">>> done. Combined report: $OUT_DIR/report.json  (+ report.md)"
echo ">>> per-testcase JSON + pod logs under: $OUT_DIR/  and  $OUT_DIR/logs/"
