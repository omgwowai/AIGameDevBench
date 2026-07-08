#!/usr/bin/env bash
# build_runner_image.sh — build (and optionally push) the AIGameDevBench runner
# image with the LATEST agentic-game-development plugin + claude CLI baked in.
#
# It does two things run_k8s_matrix.sh's raw `docker build` cannot:
#   1. Refresh + vendor the plugin: git-pull the agentic-game-development repo and
#      copy plugins/agentic-game-development-superpowers into docker/vendor/agd-plugin
#      so the Dockerfile's `COPY docker/vendor/agd-plugin` picks up the current skills.
#   2. Bake in the claude CLI via the Dockerfile's HARNESS_INSTALL build-arg.
#
# The resulting image runs `claude -p {task} --dangerously-skip-permissions
# --plugin-dir /opt/agd-plugin` per testcase (see the k8s Secret / HARNESS_CMD).
#
# Usage:
#   scripts/build_runner_image.sh -i harbor.omgwow.ai/<proj>/aigdbench-runner:latest [--push]
#
# Options (env in parens):
#   -i IMAGE      full registry ref                              (IMAGE, required)
#   -p, --push    docker push after build                        (PUSH=1)
#   -P DIR        agentic-game-development checkout to vendor from
#                                    (PLUGIN_REPO, default: ../agentic-game-development)
#   --no-pull     skip `git pull` in the plugin repo (use as-is)
#   -g VERSION    Godot version build-arg                        (GODOT_VERSION, default 4.5-stable)
#   -h            help
#
# Notes:
#   * `docker`/`sudo docker` selection: if the invoking user is not in the docker
#     group we retry through `sg docker -c`. Set DOCKER='sudo docker' to force.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IMAGE="${IMAGE:-}"
PUSH="${PUSH:-0}"
PLUGIN_REPO="${PLUGIN_REPO:-$REPO_ROOT/../agentic-game-development}"
DO_PULL=1
GODOT_VERSION="${GODOT_VERSION:-4.5-stable}"
PLUGIN_SUBDIR="plugins/agentic-game-development-superpowers"
VENDOR_DIR="$REPO_ROOT/docker/vendor/agd-plugin"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i) IMAGE="$2"; shift 2;;
    -p|--push) PUSH=1; shift;;
    -P) PLUGIN_REPO="$2"; shift 2;;
    --no-pull) DO_PULL=0; shift;;
    -g) GODOT_VERSION="$2"; shift 2;;
    -h|--help) sed -n '2,40p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

[[ -n "$IMAGE" ]] || { echo "ERROR: -i IMAGE (full registry ref) is required" >&2; exit 2; }

# --- docker invocation: prefer plain docker, fall back to `sg docker -c` -------
# run_k8s_matrix.sh calls bare `docker`; keep this wrapper consistent so a fresh
# docker-group membership (not yet in the login session) still works.
if [[ -n "${DOCKER:-}" ]]; then
  run_docker() { $DOCKER "$@"; }
elif docker ps >/dev/null 2>&1; then
  run_docker() { docker "$@"; }
elif sg docker -c 'docker ps >/dev/null 2>&1'; then
  echo ">>> using 'sg docker -c' (docker group not yet in this login session)"
  run_docker() { sg docker -c "docker $(printf '%q ' "$@")"; }
else
  echo "ERROR: cannot reach the docker daemon (tried docker and 'sg docker')." >&2
  echo "       Try: newgrp docker   (or run with DOCKER='sudo docker')" >&2
  exit 1
fi

# --- 1. Refresh + vendor the plugin ------------------------------------------
[[ -d "$PLUGIN_REPO/$PLUGIN_SUBDIR/skills" ]] \
  || { echo "ERROR: plugin not found at $PLUGIN_REPO/$PLUGIN_SUBDIR (set -P)" >&2; exit 1; }

if [[ "$DO_PULL" == "1" ]]; then
  echo ">>> git pull latest plugin in $PLUGIN_REPO ..."
  git -C "$PLUGIN_REPO" pull --ff-only 2>&1 | tail -2 || \
    echo ">>> WARN: git pull failed; vendoring current checkout"
fi

PLUGIN_VER="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("version","?"))' \
  "$PLUGIN_REPO/$PLUGIN_SUBDIR/.claude-plugin/plugin.json" 2>/dev/null || echo '?')"
echo ">>> vendoring plugin version $PLUGIN_VER -> docker/vendor/agd-plugin"
rm -rf "$VENDOR_DIR"
mkdir -p "$VENDOR_DIR"
cp -r "$PLUGIN_REPO/$PLUGIN_SUBDIR/." "$VENDOR_DIR/"
echo ">>> skills vendored: $(ls "$VENDOR_DIR/skills" | wc -l)"

# --- 2. Build -----------------------------------------------------------------
echo ">>> building $IMAGE (Godot $GODOT_VERSION, claude CLI + plugin $PLUGIN_VER)..."
run_docker build \
  --build-arg "GODOT_VERSION=$GODOT_VERSION" \
  --build-arg "HARNESS_INSTALL=npm i -g @anthropic-ai/claude-code" \
  -f docker/Dockerfile \
  -t "$IMAGE" .

# --- 3. Push ------------------------------------------------------------------
if [[ "$PUSH" == "1" ]]; then
  echo ">>> pushing $IMAGE ..."
  run_docker push "$IMAGE"
else
  echo ">>> built (not pushed). Re-run with --push to publish."
fi

echo ">>> done: $IMAGE  (plugin $PLUGIN_VER)"
