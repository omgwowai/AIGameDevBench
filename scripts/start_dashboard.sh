#!/usr/bin/env bash
# One-shot launcher for the AIGameDevBench web dashboard.
#
# The dashboard (Reports + Testcases + Contents + Run + Status + Webhooks tabs) is one
# server on :8000, run in a detached tmux session so it survives your shell
# closing. Webhook messages are shown IN the dashboard's Webhooks tab (reading
# .orchestrator/webhooks.jsonl). The Run tab launches the REAL production
# benchmark: docker image (reused) + one Kubernetes Job per testcase, harness
# from the cluster Secret -- NOT a local run. Results become a normal report.
#
# It ALSO starts a webhook receiver on :8899 (POST /trigger) that captures the
# k8s github-webhook receiver's forwarded deliveries into the same log the
# Webhooks tab reads (record-only, no benchmark auto-run). The k8s receiver
# forwards to BENCH_TRIGGER_URL = http://<this-host>:8899/trigger; if nothing
# listens there the deliveries are lost ("fetch failed"), so this keeps them.
#
# Port :8000 is opened PERMANENTLY at the OS level (iptables INPUT ACCEPT,
# persisted in /etc/iptables/rules.v4), so by default this script does NOT touch
# iptables. Pass --firewall to have the script open/close the port itself.
# (:8899 is already firewalled to the receiver's subnet 10.0.21.0/24.)
#
# Usage (from anywhere):
#   scripts/start_dashboard.sh                 # start (port already open)
#   scripts/start_dashboard.sh --stop          # stop (leaves port open)
#   scripts/start_dashboard.sh --foreground    # dashboard only, foreground (no receiver)
#   scripts/start_dashboard.sh --firewall      # ALSO open/close the port in iptables
#
# Env overrides:
#   PORT(8000) HOST(0.0.0.0) REPORTS_DIR TESTCASES_DIR SESSION(aigdbench-web)
#   ALLOW_RUN(1) WEBHOOK_LOG(.orchestrator/webhooks.jsonl)
#   WEBHOOK_RECV_PORT(8899; 0=off) WEBHOOK_TOKEN(empty=accept all)
#   WEBHOOK_AUTORUN_MODE(candidate|matrix|off) WEBHOOK_AUTO_RELEASE(1)
#   PLUGIN_REPO(../agentic-game-development) IMAGE_REPO RESULTS_ROOT
#   RUNNER_IMAGE K8S_NAMESPACE(default) HARNESS_SECRET(aigdbench-harness)
#   IMAGE_TESTCASES_DIR(/app/testcases_filtered) LOCAL_TESTCASES_DIR JOBS(16)
#   ALLOW_CIDR(0.0.0.0/0 => open to everyone; set e.g. 10.0.21.0/24 to restrict)
set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
REPORTS_DIR="${REPORTS_DIR:-$REPO_ROOT/dashboard_reports}"
TESTCASES_DIR="${TESTCASES_DIR:-$REPO_ROOT/testcases_filtered}"
SESSION="${SESSION:-aigdbench-web}"
ALLOW_RUN="${ALLOW_RUN:-1}"
WEBHOOK_LOG="${WEBHOOK_LOG:-$REPO_ROOT/.orchestrator/webhooks.jsonl}"
ALLOW_CIDR="${ALLOW_CIDR:-0.0.0.0/0}"
# Webhook receiver: listens for the k8s github-webhook receiver's forwarded
# deliveries (BENCH_TRIGGER_URL = http://<this-host>:8899/trigger) and appends
# them to $WEBHOOK_LOG so the dashboard's Webhooks tab shows them.
# Set WEBHOOK_RECV_PORT=0 to disable. WEBHOOK_TOKEN (empty = accept all; the
# port is already firewalled to the receiver subnet).
WEBHOOK_RECV_PORT="${WEBHOOK_RECV_PORT:-8899}"
WEBHOOK_TOKEN="${WEBHOOK_TOKEN:-}"
# WEBHOOK_AUTORUN_MODE on each accepted opened PR:
#   candidate (default) -> run scripts/bench-candidate.sh, which builds the
#     plugin from origin/main + cherry-pick(PR commit) (== the plugin AS IF the
#     PR were merged), benchmarks it, and (with WEBHOOK_AUTO_RELEASE=1) merges +
#     releases only if it beats the historical best. Correct "merged plugin" eval.
#   matrix -> POST /api/runs/start (shared :latest image; does NOT reflect the PR).
#   off -> record only.
WEBHOOK_AUTORUN_MODE="${WEBHOOK_AUTORUN_MODE:-candidate}"
WEBHOOK_AUTO_RELEASE="${WEBHOOK_AUTO_RELEASE:-1}"   # candidate: merge+release winners
# Max concurrent k8s Jobs. Match the node's real capacity: on a 2-core node with
# 250m CPU requests (see run_k8s_matrix.sh), ~6 Jobs fit after system pods.
# Setting this far above capacity just parks the excess in Pending. Raise on a
# bigger cluster.
AUTORUN_JOBS="${AUTORUN_JOBS:-6}"
# Per-testcase harness timeout for auto-runs. The matrix sets each k8s Job's
# activeDeadlineSeconds = TIMEOUT + 300, so this also bounds the hard kill.
# 2400s (40 min) leaves headroom for the heavy git-type survey-history_* cases
# (godot-open-rpg + Dialogic; ~6 min harness even when passing) which, under
# concurrency, exceeded the old 1200s default and were killed with
# DeadlineExceeded -> empty pod log -> spurious score 0.
AUTORUN_TIMEOUT="${AUTORUN_TIMEOUT:-2400}"
# Run tab = real docker + k8s matrix (one Job per testcase on this image).
RUNNER_IMAGE="${RUNNER_IMAGE:-harbor.omgwow.ai/beaver_hub-public/aigdbench-runner:latest}"
IMAGE_REPO="${IMAGE_REPO:-harbor.omgwow.ai/beaver_hub-public/aigdbench-runner}"
# Harbor host + k8s docker-registry secret with the robot cred, used to (a) log
# docker in for candidate push and (b) list Run-tab selectable image tags.
HARBOR_HOST="${HARBOR_HOST:-harbor.omgwow.ai}"
HARBOR_PULL_SECRET="${HARBOR_PULL_SECRET:-harbor-cred}"
PLUGIN_REPO="${PLUGIN_REPO:-$REPO_ROOT/../agentic-game-development}"
RESULTS_ROOT="${RESULTS_ROOT:-$REPO_ROOT/results}"
LOCAL_TESTCASES_DIR="${LOCAL_TESTCASES_DIR:-$TESTCASES_DIR}"
K8S_NAMESPACE="${K8S_NAMESPACE:-default}"
HARNESS_SECRET="${HARNESS_SECRET:-aigdbench-harness}"
IMAGE_TESTCASES_DIR="${IMAGE_TESTCASES_DIR:-/app/testcases_filtered}"
JOBS="${JOBS:-6}"   # Run-tab concurrency; match node capacity (see AUTORUN_JOBS)

# By default the script does NOT touch iptables: :8000 is opened permanently at
# the OS level (persisted in /etc/iptables/rules.v4), so the script must not
# close it on --stop. Pass --firewall to have the script open/close the port
# itself (e.g. when using a non-default port).
FOREGROUND=0; STOP=0; DO_FIREWALL=0
for arg in "$@"; do
  case "$arg" in
    --foreground|-f) FOREGROUND=1 ;;
    --stop)          STOP=1 ;;
    --firewall)      DO_FIREWALL=1 ;;
    --no-firewall)   DO_FIREWALL=0 ;;
    -h|--help)       sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# --- iptables helpers -------------------------------------------------------
# INPUT ends in REJECT, so each served port needs an explicit ACCEPT inserted
# ABOVE that reject. -I INPUT 1 puts it at the top. Idempotent via -C check.
fw_open() {
  local port="$1"
  [[ "$DO_FIREWALL" == "1" ]] || return 0
  command -v iptables >/dev/null 2>&1 || { echo "  (no iptables; skip open :$port)"; return 0; }
  if sudo -n iptables -C INPUT -s "$ALLOW_CIDR" -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    echo "  firewall: :$port already open for $ALLOW_CIDR"
  elif sudo -n iptables -I INPUT 1 -s "$ALLOW_CIDR" -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    echo "  firewall: opened :$port for $ALLOW_CIDR"
  else
    echo "  WARNING: could not open :$port in iptables (need sudo). External access will fail." >&2
  fi
}
fw_close() {
  local port="$1"
  [[ "$DO_FIREWALL" == "1" ]] || return 0
  command -v iptables >/dev/null 2>&1 || return 0
  # Delete every matching rule (there should be at most one).
  while sudo -n iptables -C INPUT -s "$ALLOW_CIDR" -p tcp --dport "$port" -j ACCEPT 2>/dev/null; do
    sudo -n iptables -D INPUT -s "$ALLOW_CIDR" -p tcp --dport "$port" -j ACCEPT 2>/dev/null || break
    echo "  firewall: closed :$port for $ALLOW_CIDR"
  done
}

# --- stop mode --------------------------------------------------------------
if [[ "$STOP" == "1" ]]; then
  if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION" && echo "stopped tmux session '$SESSION'"
  else
    for p in "$PORT" "$WEBHOOK_RECV_PORT"; do
      [[ "$p" =~ ^[0-9]+$ && "$p" -gt 0 ]] || continue
      pid="$(ss -ltnp 2>/dev/null | grep ":$p " | grep -oP 'pid=\K[0-9]+' | head -1 || true)"
      [[ -n "$pid" ]] && kill "$pid" && echo "killed pid $pid on :$p"
    done
  fi
  fw_close "$PORT"
  exit 0
fi

# --- pick a python interpreter that has the package + its deps --------------
pick_python() {
  for cand in \
      "$REPO_ROOT/.venv/bin/python3" \
      "/tmp/AIGameDevBench/.venv/bin/python3" \
      "python3" "python"; do
    command -v "$cand" >/dev/null 2>&1 || [[ -x "$cand" ]] || continue
    PYBIN="$cand"; return 0
  done
  echo "ERROR: no python3 found" >&2; exit 1
}
pick_python
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
if ! "$PYBIN" -c "import aigamedevbench, godot_parser" >/dev/null 2>&1; then
  echo "ERROR: '$PYBIN' cannot import aigamedevbench + godot_parser." >&2
  echo "       Install deps first, e.g.:  $PYBIN -m pip install -e '.[dev]'" >&2
  exit 1
fi

# --- assemble the dashboard command -----------------------------------------
ARGS=(-m aigamedevbench.cli serve
      --host "$HOST" --port "$PORT"
      --reports-dir "$REPORTS_DIR" --testcases-dir "$TESTCASES_DIR"
      --webhook-log "$WEBHOOK_LOG"
      --runner-image "$RUNNER_IMAGE" --k8s-namespace "$K8S_NAMESPACE"
      --harness-secret "$HARNESS_SECRET" --image-testcases-dir "$IMAGE_TESTCASES_DIR"
      --image-repo "$IMAGE_REPO" --harbor-secret "$HARBOR_PULL_SECRET"
      --jobs "$JOBS"
      --no-open-browser)
[[ "$ALLOW_RUN" == "1" ]] && ARGS+=(--allow-run) || ARGS+=(--no-allow-run)
mkdir -p "$REPORTS_DIR" "$(dirname "$WEBHOOK_LOG")"
touch "$WEBHOOK_LOG"

if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "ERROR: port $PORT is already in use. Stop it first: $0 --stop" >&2
  exit 1
fi

lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
url_host="${lan_ip:-<this-node-ip>}"

# Whether to run the webhook receiver (records forwarded deliveries to the log
# the dashboard reads). Disabled if WEBHOOK_RECV_PORT is 0 or the port is busy.
RECV_ON=0
if [[ "$WEBHOOK_RECV_PORT" =~ ^[0-9]+$ && "$WEBHOOK_RECV_PORT" -gt 0 ]]; then
  if ss -ltn 2>/dev/null | grep -q ":$WEBHOOK_RECV_PORT "; then
    echo "WARNING: webhook receiver port $WEBHOOK_RECV_PORT is busy; receiver NOT started." >&2
  else
    RECV_ON=1
  fi
fi
# Build the receiver argv (used by both tmux + nohup paths).
RECV_ARGS=("$REPO_ROOT/scripts/webhook_receiver.py"
           --host 0.0.0.0 --port "$WEBHOOK_RECV_PORT" --log "$WEBHOOK_LOG")
[[ -n "$WEBHOOK_TOKEN" ]] && RECV_ARGS+=(--token "$WEBHOOK_TOKEN")
# Ensure local docker can PUSH candidate images to Harbor. bench-candidate.sh
# builds :<sha> per PR and pushes it for the k8s Jobs to pull; if docker is
# logged in as a pull-only human account the push fails and every candidate
# aborts (this bit us on PR #31). Reuse the SAME robot the cluster's harbor-cred
# pull secret uses (it has pull+push+delete). Idempotent: skip if a push-scoped
# token is already obtainable. (HARBOR_HOST/HARBOR_PULL_SECRET defined up top.)
ensure_harbor_push_login() {
  command -v docker >/dev/null 2>&1 || return 1
  command -v kubectl >/dev/null 2>&1 || return 1
  local creds user pass
  creds="$(kubectl -n "$K8S_NAMESPACE" get secret "$HARBOR_PULL_SECRET" \
             -o jsonpath='{.data.\.dockerconfigjson}' 2>/dev/null | base64 -d 2>/dev/null)"
  [[ -n "$creds" ]] || { echo "WARNING: cannot read $HARBOR_PULL_SECRET for docker push login." >&2; return 1; }
  user="$(printf '%s' "$creds" | python3 -c "import sys,json;print(json.load(sys.stdin)['auths']['$HARBOR_HOST']['username'])" 2>/dev/null)"
  pass="$(printf '%s' "$creds" | python3 -c "import sys,json;print(json.load(sys.stdin)['auths']['$HARBOR_HOST']['password'])" 2>/dev/null)"
  [[ -n "$user" && -n "$pass" ]] || { echo "WARNING: $HARBOR_PULL_SECRET missing $HARBOR_HOST username/password." >&2; return 1; }
  # NOTE: never let the shell interpolate the robot name (contains a literal '$').
  if printf '%s' "$pass" | docker login "$HARBOR_HOST" -u "$user" --password-stdin >/dev/null 2>&1; then
    echo "    docker: logged in to $HARBOR_HOST as $user (push enabled)"
    return 0
  fi
  echo "WARNING: docker login to $HARBOR_HOST as $user failed; candidate push may fail." >&2
  return 1
}

# Resolve the effective auto-run mode. candidate needs a plugin git checkout +
# docker + kubectl; if any is missing, fall back to record-only (off).
AUTORUN_MODE="$WEBHOOK_AUTORUN_MODE"
if [[ "$AUTORUN_MODE" == "candidate" ]]; then
  if [[ ! -d "$PLUGIN_REPO/.git" ]]; then
    echo "WARNING: PLUGIN_REPO ($PLUGIN_REPO) is not a git checkout; auto-run disabled." >&2
    AUTORUN_MODE="off"
  elif ! command -v docker >/dev/null 2>&1 || ! command -v kubectl >/dev/null 2>&1; then
    echo "WARNING: docker/kubectl missing; candidate auto-run disabled." >&2
    AUTORUN_MODE="off"
  else
    ensure_harbor_push_login || true   # warn but still run; push may be pre-authed
  fi
elif [[ "$AUTORUN_MODE" == "matrix" && "$ALLOW_RUN" != "1" ]]; then
  AUTORUN_MODE="off"   # no /api/runs/start to hit
fi
RECV_ARGS+=(--autorun-mode "$AUTORUN_MODE"
            --autorun-jobs "$AUTORUN_JOBS" --autorun-timeout "$AUTORUN_TIMEOUT")
if [[ "$AUTORUN_MODE" == "matrix" ]]; then
  RECV_ARGS+=(--autorun-url "http://127.0.0.1:$PORT/api/runs/start")
elif [[ "$AUTORUN_MODE" == "candidate" ]]; then
  RECV_ARGS+=(--repo-root "$REPO_ROOT" --plugin-repo "$PLUGIN_REPO"
              --image-repo "$IMAGE_REPO" --secret "$HARNESS_SECRET"
              --image-testcases-dir "$IMAGE_TESTCASES_DIR"
              --local-testcases-dir "$LOCAL_TESTCASES_DIR"
              --namespace "$K8S_NAMESPACE" --results-root "$RESULTS_ROOT"
              --reports-dir "$REPORTS_DIR"
              --dashboard-url "http://127.0.0.1:$PORT/api/runs/external")
  [[ "$WEBHOOK_AUTO_RELEASE" == "1" ]] && RECV_ARGS+=(--auto-release)
fi

banner() {
  echo "--- AIGameDevBench dashboard"
  echo "    local:    http://127.0.0.1:$PORT/"
  [[ "$HOST" == "0.0.0.0" || "$HOST" == "::" ]] && \
    echo "    network:  http://$url_host:$PORT/"
  echo "    tabs:     Reports · Testcases · Contents · Run · Status · Webhooks"
  echo "    webhooks: $WEBHOOK_LOG"
  if [[ "$RECV_ON" == "1" ]]; then
    echo "    receiver: POST http://$url_host:$WEBHOOK_RECV_PORT/trigger -> Webhooks tab"
    case "$AUTORUN_MODE" in
      candidate)
        echo "    auto-run: opened PR -> bench-candidate.sh (main+PR merged plugin)"$'\n'"              gate$([[ "$WEBHOOK_AUTO_RELEASE" == "1" ]] && echo " + auto-merge/release" || echo " only") · plugin=$PLUGIN_REPO" ;;
      matrix)
        echo "    auto-run: opened PR -> matrix over all testcases (:latest, jobs=$AUTORUN_JOBS)" ;;
      *)
        echo "    auto-run: off (record only; run manually from the Run tab)" ;;
    esac
  else
    echo "    receiver: off (webhooks only appear if something else writes the log)"
  fi
  echo "    reports:  $REPORTS_DIR"
  if [[ "$ALLOW_RUN" == "1" ]]; then
    echo "    run tab:  docker+k8s matrix (image=$RUNNER_IMAGE ns=$K8S_NAMESPACE secret=$HARNESS_SECRET)"
  else
    echo "    run tab:  disabled"
  fi
  if [[ "$DO_FIREWALL" == "1" ]]; then
    echo "    firewall: script-managed, opened for $ALLOW_CIDR (closed on --stop)"
  else
    echo "    firewall: not managed by script; :$PORT is permanently open (rules.v4)"
  fi
}

# --- foreground -------------------------------------------------------------
if [[ "$FOREGROUND" == "1" ]]; then
  fw_open "$PORT"
  banner
  echo "    (foreground; Ctrl-C to stop. Port stays open; run '$0 --stop' to close it.)"
  exec "$PYBIN" "${ARGS[@]}"
fi

# --- background via tmux (preferred) ----------------------------------------
if command -v tmux >/dev/null 2>&1; then
  tmux has-session -t "$SESSION" 2>/dev/null && {
    echo "ERROR: tmux session '$SESSION' already exists. Stop it: $0 --stop" >&2
    exit 1; }
  dash_cmd="cd $(printf %q "$REPO_ROOT") && PYTHONPATH=$(printf %q "$PYTHONPATH") $(printf %q "$PYBIN")"
  for a in "${ARGS[@]}"; do dash_cmd+=" $(printf %q "$a")"; done
  tmux new-session -d -s "$SESSION" -n dashboard "$dash_cmd"
  fw_open "$PORT"
  if [[ "$RECV_ON" == "1" ]]; then
    recv_cmd="cd $(printf %q "$REPO_ROOT") && $(printf %q "$PYBIN")"
    for a in "${RECV_ARGS[@]}"; do recv_cmd+=" $(printf %q "$a")"; done
    tmux new-window -t "$SESSION" -n webhook-receiver "$recv_cmd"
  fi
  sleep 1
  banner
  echo "    tmux:     attach with 'tmux attach -t $SESSION' ; stop with '$0 --stop'"
  exit 0
fi

# --- background without tmux (nohup fallback) -------------------------------
LOG="$REPO_ROOT/dashboard.log"
nohup setsid "$PYBIN" "${ARGS[@]}" >"$LOG" 2>&1 < /dev/null &
fw_open "$PORT"
if [[ "$RECV_ON" == "1" ]]; then
  nohup setsid "$PYBIN" "${RECV_ARGS[@]}" \
      >"$REPO_ROOT/webhook_receiver.log" 2>&1 < /dev/null &
fi
sleep 1
banner
echo "    log:      $LOG (no tmux; stop with '$0 --stop')"
