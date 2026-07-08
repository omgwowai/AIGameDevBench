#!/usr/bin/env bash
# Runs exactly ONE testcase inside this container and writes a per-testcase
# JSON report to $OUT_DIR. The k8s matrix script launches one Job per testcase.
#
# Because cluster Pods usually have no shared writable storage back to the
# launcher, this ALSO prints the report to stdout wrapped in unambiguous
# markers, so `kubectl logs <pod>` is a reliable transport for the result:
#
#   <<<AIGDBENCH_REPORT_BEGIN testcase=<id>>>>
#   { ...report json... }
#   <<<AIGDBENCH_REPORT_END testcase=<id> exit=<code>>>>
set -uo pipefail

TESTCASE="${TESTCASE:?set TESTCASE to a testcase id (see 'aigdbench list')}"
TESTCASES_DIR="${TESTCASES_DIR:-/app/testcases}"
OUT_DIR="${OUT_DIR:-/out}"
DRIVER="${DRIVER:-patch}"
GODOT_BINARY="${GODOT_BINARY:-godot}"
TIMEOUT="${TIMEOUT:-900}"
HARNESS_ID="${HARNESS_ID:-$DRIVER}"

mkdir -p "$OUT_DIR" "$OUT_DIR/logs"
report="$OUT_DIR/${TESTCASE}.json"
runlog="$OUT_DIR/logs/${TESTCASE}.run.log"

echo "[entrypoint] testcase=$TESTCASE driver=$DRIVER godot=$GODOT_BINARY tcdir=$TESTCASES_DIR" | tee "$runlog"

# Build the driver-specific args.
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
    # Golden fix shipped alongside the testcase. Validates the full pipeline
    # (patch should score 1.00). Authored folder cases carry fix.diff; the
    # git-derived survey-history cases carry good.diff — accept either.
    patch_file=""
    for cand in fix.diff good.diff; do
      if [[ -f "$TESTCASES_DIR/$TESTCASE/$cand" ]]; then
        patch_file="$TESTCASES_DIR/$TESTCASE/$cand"
        break
      fi
    done
    if [[ -z "$patch_file" ]]; then
      echo "[entrypoint] ERROR: no golden patch (fix.diff|good.diff) for $TESTCASE under $TESTCASES_DIR/$TESTCASE" | tee -a "$runlog"
      exit 3
    fi
    echo "[entrypoint] golden patch: $patch_file" | tee -a "$runlog"
    args+=(--patch "$patch_file")
    ;;
  command)
    # Real AI harness. Provide the template via HARNESS_CMD, e.g.
    #   -e HARNESS_CMD='claude -p {task} --dangerously-skip-permissions'
    : "${HARNESS_CMD:?DRIVER=command requires HARNESS_CMD (e.g. 'claude -p {task}')}"
    args+=(--harness-cmd "$HARNESS_CMD")
    ;;
  noop)
    : # noop should score 0.00 — useful as a sanity baseline
    ;;
  *)
    echo "[entrypoint] ERROR: unknown DRIVER='$DRIVER' (noop|patch|command)" | tee -a "$runlog"
    exit 2
    ;;
esac

# Folder-type testcases are self-contained; git-type ones must run inside the
# target game repo. We cd into the testcase's baseline when present so both
# kinds work from the same image.
run_from="$TESTCASES_DIR/$TESTCASE/baseline"
[[ -d "$run_from" ]] || run_from="$TESTCASES_DIR"

set +e
( cd "$run_from" && aigdbench "${args[@]}" ) 2>&1 | tee -a "$runlog"
status=${PIPESTATUS[0]}
set -e

# Guarantee a report exists even if aigdbench crashed, so aggregation never
# has a silent hole.
if [[ ! -f "$report" ]]; then
  cat > "$report" <<EOF
{"harness": "$HARNESS_ID", "count": 1, "mean_score": 0.0,
 "testcases": [{"testcase_id": "$TESTCASE", "score": 0.0, "l0_l1_pass": false,
   "verifier_result": {"status": "error", "error": "runner exited $status, no report"}}]}
EOF
  echo "[entrypoint] WARN: synthesized error report (exit $status)" | tee -a "$runlog"
fi

# Emit the report to stdout so the orchestrator can collect it from pod logs
# even with no shared storage. Markers are on their own lines and carry the
# testcase id so a scraper can pair them unambiguously.
echo "<<<AIGDBENCH_REPORT_BEGIN testcase=${TESTCASE}>>>"
cat "$report"
echo ""
echo "<<<AIGDBENCH_REPORT_END testcase=${TESTCASE} exit=${status}>>>"

echo "[entrypoint] done testcase=$TESTCASE exit=$status report=$report" | tee -a "$runlog"
exit "$status"
