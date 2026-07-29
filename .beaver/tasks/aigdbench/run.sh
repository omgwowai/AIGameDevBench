#!/usr/bin/env bash
# DRAFT wrapper for the aigdbench Task entrypoint.
#
# Faithful restructuring of docker/entrypoint.sh: instead of reading env vars
# (TESTCASE/DRIVER/HARNESS_CMD/TIMEOUT) it reads the schema-validated business
# contract from /in/input.json, and it writes /out/result.json for the platform
# to harvest (no AIGDBENCH_REPORT stdout markers — the Task output contract
# replaces that kubectl-logs transport hack).
#
# Same two guarantees as entrypoint.sh:
#   1. always emit result.json (synthesize an error report on crash);
#   2. cd into <testcase>/baseline when present, else the testcases dir.
#
# NOT wired to a cluster; jq/python availability assumed from beaver-godot.
set -uo pipefail

IN="${IN_FILE:-/in/input.json}"
OUT_DIR="${OUT_DIR:-/out}"
mkdir -p "$OUT_DIR" "$OUT_DIR/logs"
status_file="$OUT_DIR/status.jsonl"
SEQ=0
report_phase() { SEQ=$((SEQ+1)); echo "{\"seq\":$SEQ,\"phase\":\"$1\"${2:-}}" >> "$status_file"; }

report_phase parse-input
# --- read the input contract (was env vars) ---
TESTCASE=$(python3 -c "import json,sys;print(json.load(open('$IN'))['testcase'])")
DRIVER=$(python3 -c "import json;print(json.load(open('$IN')).get('driver','patch'))")
TESTCASES_DIR=$(python3 -c "import json;print(json.load(open('$IN')).get('testcases_dir','/app/testcases'))")
TIMEOUT=$(python3 -c "import json;print(json.load(open('$IN')).get('timeout_seconds',900))")
ENGINE=$(python3 -c "import json;print(json.load(open('$IN')).get('engine','godot-4.5'))")
HARNESS_CMD=$(python3 -c "import json;print(json.load(open('$IN')).get('harness_cmd',''))")
HARNESS_ID="$DRIVER"
case "$ENGINE" in
  godot-3.6) GODOT_BINARY="godot3" ;;
  *)         GODOT_BINARY="godot" ;;
esac

report="$OUT_DIR/result.json"
runlog="$OUT_DIR/logs/${TESTCASE}.run.log"
echo "[task] testcase=$TESTCASE driver=$DRIVER engine=$ENGINE tcdir=$TESTCASES_DIR" | tee "$runlog"

args=(run
  --testcases-dir "$TESTCASES_DIR"
  --testcase "$TESTCASE"
  --harness "$HARNESS_ID"
  --driver "$DRIVER"
  --godot-binary "$GODOT_BINARY"
  --timeout "$TIMEOUT"
  --log-dir "$OUT_DIR/logs"
  --report "$report")

case "$DRIVER" in
  patch)
    patch_file=""
    for cand in fix.diff good.diff; do
      [[ -f "$TESTCASES_DIR/$TESTCASE/$cand" ]] && { patch_file="$TESTCASES_DIR/$TESTCASE/$cand"; break; }
    done
    if [[ -z "$patch_file" ]]; then
      echo "[task] ERROR: no golden patch for $TESTCASE" | tee -a "$runlog"; exit 3
    fi
    args+=(--patch "$patch_file")
    ;;
  command)
    if [[ -z "$HARNESS_CMD" ]]; then
      echo "[task] ERROR: driver=command requires input.harness_cmd" | tee -a "$runlog"; exit 3
    fi
    args+=(--harness-cmd "$HARNESS_CMD")
    ;;
  noop) : ;;
  *) echo "[task] ERROR: unknown driver='$DRIVER'" | tee -a "$runlog"; exit 2 ;;
esac

run_from="$TESTCASES_DIR/$TESTCASE/baseline"
[[ -d "$run_from" ]] || run_from="$TESTCASES_DIR"

report_phase scoring ",\"fields\":{\"testcase\":\"$TESTCASE\",\"driver\":\"$DRIVER\"}"
set +e
( cd "$run_from" && aigdbench "${args[@]}" ) 2>&1 | tee -a "$runlog"
status=${PIPESTATUS[0]}
set -e

if [[ ! -f "$report" ]]; then
  cat > "$report" <<EOF
{"harness": "$HARNESS_ID", "count": 1, "mean_score": 0.0,
 "testcases": [{"testcase_id": "$TESTCASE", "score": 0.0, "l0_l1_pass": false,
   "verifier_result": {"status": "error", "error": "runner exited $status, no report"}}]}
EOF
  echo "[task] WARN: synthesized error report (exit $status)" | tee -a "$runlog"
fi

report_phase done
echo "[task] done testcase=$TESTCASE exit=$status report=$report" | tee -a "$runlog"
exit "$status"
