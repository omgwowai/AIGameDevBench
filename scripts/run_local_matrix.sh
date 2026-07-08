#!/usr/bin/env bash
# run_local_matrix.sh — build the AIGameDevBench image from source, then fan out
# ONE container per testcase concurrently on THIS host (no cluster). Each
# container runs a single testcase and drops a per-testcase JSON in ./results.
# When all containers finish, aggregate into a combined report + Markdown.
#
# This is the local sibling of scripts/run_k8s_matrix.sh: same image and
# entrypoint, but scheduled with `docker`/`podman run` instead of k8s Jobs.
# Use it to validate the whole pipeline before pushing to a real cluster.
#
# Container engine: docker OR podman (auto-detected, override with -e/ENGINE).
# On NFS-backed home dirs, rootless podman's overlay store can't set xattrs, so
# by default we point podman at a LOCAL graphroot with the vfs driver. Disable
# with --no-podman-fix if your podman store already works.
#
# Usage:
#   scripts/run_local_matrix.sh [options]
#
# Options (also settable via env):
#   -j N            max concurrent containers        (JOBS, default: nproc, capped 8)
#   -d DRIVER       noop | patch | command           (DRIVER, default: patch)
#   -c "CMD {task}" harness command (DRIVER=command)  (HARNESS_CMD)
#   -t "id id ..."  explicit testcase ids            (default: all discovered)
#   -D DIR          testcases dir INSIDE the image    (TESTCASES_DIR, default: /app/testcases)
#   -o DIR          results/output dir on host        (OUT_DIR, default: ./results)
#   -i IMAGE        image tag                         (IMAGE, default: aigamedevbench:local)
#   -g VERSION      Godot version build-arg           (GODOT_VERSION, default: 4.5-stable)
#   -T SECONDS      per-testcase timeout             (TIMEOUT, default: 900)
#   -H CMD          build-arg to install a harness    (HARNESS_INSTALL)
#   -e ENGINE       docker | podman                   (ENGINE, default: auto)
#   --no-build      skip image build (reuse image)
#   --no-podman-fix don't relocate podman store to local vfs
#   -h              help
#
# Examples:
#   scripts/run_local_matrix.sh -d patch -j 4          # golden patch, 4 at a time
#   scripts/run_local_matrix.sh -d noop                # sanity: all should be 0
#   scripts/run_local_matrix.sh -d patch -t 'ability-cooldown-gate damage-formula-refactor'
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IMAGE="${IMAGE:-aigamedevbench:local}"
DRIVER="${DRIVER:-patch}"
_ncpu="$(nproc 2>/dev/null || echo 4)"
JOBS="${JOBS:-$(( _ncpu > 8 ? 8 : _ncpu ))}"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/results}"
TESTCASES_DIR="${TESTCASES_DIR:-/app/testcases}"
GODOT_VERSION="${GODOT_VERSION:-4.5-stable}"
TIMEOUT="${TIMEOUT:-900}"
HARNESS_CMD="${HARNESS_CMD:-}"
HARNESS_INSTALL="${HARNESS_INSTALL:-}"
ENGINE="${ENGINE:-auto}"
TESTCASES=""
DO_BUILD=1
PODMAN_FIX=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    -j) JOBS="$2"; shift 2;;
    -d) DRIVER="$2"; shift 2;;
    -c) HARNESS_CMD="$2"; shift 2;;
    -t) TESTCASES="$2"; shift 2;;
    -D) TESTCASES_DIR="$2"; shift 2;;
    -o) OUT_DIR="$2"; shift 2;;
    -i) IMAGE="$2"; shift 2;;
    -g) GODOT_VERSION="$2"; shift 2;;
    -T) TIMEOUT="$2"; shift 2;;
    -H) HARNESS_INSTALL="$2"; shift 2;;
    -e) ENGINE="$2"; shift 2;;
    --no-build) DO_BUILD=0; shift;;
    --no-podman-fix) PODMAN_FIX=0; shift;;
    -h|--help) sed -n '2,55p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

# --- Pick a container engine -------------------------------------------------
if [[ "$ENGINE" == "auto" ]]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ENGINE=docker
  elif command -v podman >/dev/null 2>&1; then
    ENGINE=podman
  else
    echo "ERROR: neither a working docker daemon nor podman found" >&2; exit 1
  fi
fi

# Rootless podman on NFS: relocate the store to a local disk + vfs driver so
# image layers can be unpacked (overlay can't set xattrs on NFS). We build the
# engine invocation as an array so the flags flow to every call.
ENGINE_ARGS=()
if [[ "$ENGINE" == "podman" && "$PODMAN_FIX" == "1" ]]; then
  # Default to /tmp, not $XDG_RUNTIME_DIR: the latter is often a small tmpfs,
  # and vfs duplicates every layer, so it fills up fast. /tmp is usually a big
  # local disk. Override with PODMAN_ROOT if /tmp is unsuitable.
  PODMAN_ROOT="${PODMAN_ROOT:-/tmp/podman-store-$USER}"
  mkdir -p "$PODMAN_ROOT"
  # /tmp is local ext4 and supports xattrs, so overlay works here and (unlike
  # vfs) doesn't copy every layer — vfs blew up /tmp during the build. Override
  # the driver with PODMAN_DRIVER if /tmp can't do overlay.
  PODMAN_DRIVER="${PODMAN_DRIVER:-overlay}"
  ENGINE_ARGS=(--root "$PODMAN_ROOT" --storage-driver "$PODMAN_DRIVER")
  echo ">>> podman store: $PODMAN_ROOT ($PODMAN_DRIVER)"
fi
ENG() { "$ENGINE" "${ENGINE_ARGS[@]}" "$@"; }

echo ">>> engine: $ENGINE ${ENGINE_ARGS[*]:-}"

if [[ "$DRIVER" == "command" && -z "$HARNESS_CMD" ]]; then
  echo "ERROR: -d command requires -c 'CMD {task}'" >&2; exit 2
fi

mkdir -p "$OUT_DIR" "$OUT_DIR/logs"
# Start clean so a stale report from a previous run can't leak into aggregation.
rm -f "$OUT_DIR"/*.json 2>/dev/null || true

# --- 0. Ensure on-demand project snapshots exist BEFORE the build ------------
# The git-derived filtered cases (survey-history_*) reference shared project
# snapshots that are NOT committed (see scripts/make_snapshots.py). The Docker
# build COPYs testcases_filtered/ into the image, so any missing snapshot would
# ship a broken case. Materialise them on the host first (needs a one-time
# network clone; idempotent — existing snapshots are skipped). Skipped with
# --no-build (reusing an image that already bundles them) or when the tree has
# no snapshot.json files.
if [[ "$DO_BUILD" == "1" ]] && ls testcases_filtered/*/snapshot.json >/dev/null 2>&1; then
  echo ">>> Ensuring project snapshots for git-derived filtered cases..."
  python3 "$REPO_ROOT/scripts/make_snapshots.py" --testcases-dir testcases_filtered
fi

# --- 1. Build image from source ----------------------------------------------
if [[ "$DO_BUILD" == "1" ]]; then
  echo ">>> Building $IMAGE from source (Godot $GODOT_VERSION)..."
  ENG build \
    --build-arg "GODOT_VERSION=$GODOT_VERSION" \
    ${HARNESS_INSTALL:+--build-arg "HARNESS_INSTALL=$HARNESS_INSTALL"} \
    -f docker/Dockerfile \
    -t "$IMAGE" .
else
  echo ">>> Skipping build, reusing $IMAGE"
fi

# --- 2. Discover testcases ---------------------------------------------------
if [[ -z "$TESTCASES" ]]; then
  TESTCASES="$(ENG run --rm --entrypoint aigdbench "$IMAGE" \
               list --testcases-dir "$TESTCASES_DIR" | awk 'NF{print $1}')"
fi
# shellcheck disable=SC2206
TC_ARR=($TESTCASES)
[[ "${#TC_ARR[@]}" -gt 0 ]] || { echo "ERROR: no testcases to run" >&2; exit 1; }
echo ">>> ${#TC_ARR[@]} testcase(s), driver=$DRIVER, concurrency=$JOBS"

# --- 3. Fan out: one container per testcase, gated at $JOBS ------------------
# For DRIVER=command the harness inside the container needs its provider
# credentials. Forward any of these that are set in the host environment.
CRED_ENVS=(
  ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL
  CLAUDE_CODE_OAUTH_TOKEN
  OPENAI_API_KEY OPENAI_BASE_URL
)
CRED_ARGS=()
for _v in "${CRED_ENVS[@]}"; do
  [[ -n "${!_v:-}" ]] && CRED_ARGS+=(-e "$_v=${!_v}")
done
# Claude Code refuses --dangerously-skip-permissions as root (containers run as
# root). IS_SANDBOX=1 is the official container/CI escape hatch. Harmless for
# other harnesses.
CRED_ARGS+=(-e "IS_SANDBOX=1")

declare -A PIDS=()
launch() {
  local tc="$1"
  local cname="aigdbench_${tc//[^a-zA-Z0-9_]/_}_$$"
  ENG run --rm --name "$cname" \
    -e TESTCASE="$tc" \
    -e DRIVER="$DRIVER" \
    -e TIMEOUT="$TIMEOUT" \
    -e HARNESS_ID="$DRIVER" \
    -e TESTCASES_DIR="$TESTCASES_DIR" \
    ${HARNESS_CMD:+-e HARNESS_CMD="$HARNESS_CMD"} \
    "${CRED_ARGS[@]}" \
    -v "$OUT_DIR:/out:z" \
    "$IMAGE" \
    > "$OUT_DIR/logs/${tc}.container.log" 2>&1 &
  PIDS["$tc"]=$!
}

running() { jobs -rp | wc -l | tr -d ' '; }

for tc in "${TC_ARR[@]}"; do
  while [[ "$(running)" -ge "$JOBS" ]]; do sleep 0.3; done
  echo ">>> launch $tc"
  launch "$tc"
done

# --- 4. Wait & collect per-container exit codes ------------------------------
echo ">>> waiting for ${#PIDS[@]} container(s)..."
declare -A EXIT=()
for tc in "${!PIDS[@]}"; do
  if wait "${PIDS[$tc]}"; then EXIT[$tc]=0; else EXIT[$tc]=$?; fi
done
for tc in "${TC_ARR[@]}"; do
  printf '    %-30s exit=%s\n' "$tc" "${EXIT[$tc]:-?}"
done

# --- 5. Aggregate ------------------------------------------------------------
echo ">>> aggregating..."
python3 "$REPO_ROOT/scripts/aggregate_report.py" \
  --results-dir "$OUT_DIR" \
  --driver "$DRIVER" \
  --image "$IMAGE" \
  --harness-cmd "$HARNESS_CMD" \
  --json "$OUT_DIR/report.json" \
  --markdown "$OUT_DIR/report.md"

echo ">>> done. Combined report: $OUT_DIR/report.json  (+ report.md)"
