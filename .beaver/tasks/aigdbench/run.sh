#!/usr/bin/env bash
# .beaver/tasks/aigdbench/run.sh -- the /in -> aigdbench CLI -> /out bridge
# for the aigdbench TaskPackage (task.yaml). See docs/beaverhub-taskpackage.md.
#
# Contract (spec/v1; schema: input.schema.json / result.schema.json):
#   read  /in/input.json   (BEAVER_INPUT overrides the path)
#         { "driver": "noop"|"patch", "testcase"?: "<id>" }
#   write /out/result.json (BEAVER_OUT overrides the output root)
#         { testcase, driver, status, exit_code, mean_score, checks,
#           stdout_path, stderr_path }
#   write /out/artifacts/  (the harness's diff + changed-file copies, when any)
#
# Exit-class discipline (mirrors BeaverHub ADR-0022): a malformed/absent
# input, or an unknown/unresolvable testcase, is a CONFIG_ERROR (exit 2)
# caught BEFORE the scorer runs -- it never reaches the harness. Once the
# scorer itself runs, its own process exit code is the authoritative verdict
# and is propagated verbatim as this script's exit code; the result
# envelope's status/exit_code fields carry the same information for a
# consumer that only reads /out/result.json. A noop run that correctly
# measures mean_score=0.00 is status=success (a successful MEASUREMENT, not a
# scorer failure) -- status is derived only from the process exit code.
#
# Offline / no-secret: this TaskPackage declares no credentials and no
# network.egress capability (task.yaml). testcases/ ships as part of this
# repo's own checkout -- unlike BeaverHub's (deprecating) beaver-godot image,
# there is no separate NO-BAKE PVC to resolve.
set -euo pipefail

fail_config() { echo "aigdbench-task: $*" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

IN="${BEAVER_INPUT:-/in/input.json}"
OUT_DIR="${BEAVER_OUT:-/out}"
TESTCASES_DIR="$REPO_ROOT/testcases"
# collision-layer-precise-edit is a self-contained, folder-type, authored
# testcase confirmed healthy (noop=0.00, patch=1.00) against a real headless
# Godot 4.6.2 run as part of this change's verification (see PR body / commit
# message) -- see docs/beaverhub-taskpackage.md for how that was checked and
# why it, not the beaver-godot description's old "tetris-clear-scores"
# default (which is not present in this repo's own testcases/ corpus), is
# the default here.
DEFAULT_TESTCASE="collision-layer-precise-edit"
GODOT_BIN="${AIGDBENCH_GODOT_BINARY:-godot}"

[ -r "$IN" ] || fail_config "input not readable at $IN"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)" || fail_config "cannot resolve OUT_DIR '$OUT_DIR'"
[ -d "$TESTCASES_DIR" ] || fail_config "testcases dir not found at $TESTCASES_DIR (expected this repo's own corpus, not an external mount)"

# Parse + validate input.json without a jq dependency (python3 is a declared
# runtime.requires.binaries entry). The parser writes a "driver testcase"
# line to a temp file and its exit STATUS is checked directly (a process
# substitution would hide a non-zero python exit from `read`).
PARSED_FILE="$(mktemp)" || fail_config "cannot allocate a temp file"
trap 'rm -f "$PARSED_FILE"' EXIT
if ! python3 - "$IN" "$DEFAULT_TESTCASE" >"$PARSED_FILE" <<'PY'
import json, re, sys

path, default_tc = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        inp = json.load(fh)
except Exception as e:
    sys.stderr.write(f"input.json is not valid JSON: {e}\n")
    sys.exit(3)

if not isinstance(inp, dict):
    sys.stderr.write("input.json must be a JSON object\n")
    sys.exit(3)

allowed = {"driver", "testcase"}
extra = sorted(set(inp) - allowed)
if extra:
    sys.stderr.write(f"input.json has unknown key(s): {extra}\n")
    sys.exit(3)

driver = inp.get("driver")
if driver not in ("noop", "patch"):
    sys.stderr.write("input.json 'driver' must be one of: noop, patch\n")
    sys.exit(3)

# input.schema.json only defaults testcase when the key is absent -- an
# explicit falsy value ("", null, false) is a malformed input, not a request
# for the default, so `or default_tc` must not swallow it here.
testcase = default_tc if "testcase" not in inp else inp["testcase"]

# Enforce the exact id shape input.schema.json declares
# (^[A-Za-z0-9_][A-Za-z0-9_.-]*$) rather than a separately maintained
# blacklist, so this runtime check can't silently drift from the schema
# that documents this TaskPackage's actual contract.
if not isinstance(testcase, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", testcase):
    sys.stderr.write("input.json 'testcase' must be a bare testcase id\n")
    sys.exit(3)

print(driver, testcase)
PY
then
    fail_config "invalid input (see message above)"
fi
read -r DRIVER TESTCASE <"$PARSED_FILE"
# Defensive: strip a trailing CR so a CRLF-translating stdout (observed on a
# Windows dev shell running this via Git Bash; never expected from the
# Linux runtime this TaskPackage actually targets) can't smuggle a "\r" onto
# the end of TESTCASE and make an otherwise-valid id fail the directory check.
DRIVER="${DRIVER%$'\r'}"
TESTCASE="${TESTCASE%$'\r'}"

[ -d "$TESTCASES_DIR/$TESTCASE" ] || fail_config "unknown testcase '$TESTCASE' (no directory under $TESTCASES_DIR)"

# Resolve the aigdbench CLI. This TaskPackage does NOT install its own Python
# dependency closure over the network at run time -- this repo's harness deps
# (click, godot-parser, gdtoolkit, networkx, pyyaml/tomli) are business engine
# code, not part of the generic runtime's declared binaries (task.yaml
# deliberately does not request network.egress). The runtime image is
# expected to already carry an equivalent of `pip install .` for this repo
# (see docs/beaverhub-taskpackage.md "Known gaps" -- this is the one capability
# gap this TaskPackage does not close). Fail with a clear CONFIG_ERROR instead
# of a confusing traceback if neither the console script nor the importable
# package is present.
if command -v aigdbench >/dev/null 2>&1; then
    AIGDBENCH=(aigdbench)
elif PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -c "import aigamedevbench" >/dev/null 2>&1; then
    # `-m aigamedevbench.cli` would only import the module and exit 0 --
    # cli.py's `main()` is a click entry point with no `if __name__ ==
    # "__main__"` guard, so `-m` never actually invokes it. Call main()
    # explicitly so this fallback runs the benchmark instead of silently
    # no-op'ing (gemini-code-assist finding on PR #11).
    AIGDBENCH=(env "PYTHONPATH=$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -c "from aigamedevbench.cli import main; main()")
else
    fail_config "the 'aigamedevbench' package is not installed on this runtime (need an equivalent of 'pip install .' from $REPO_ROOT baked into the runtime image; this script does not install packages over the network -- see docs/beaverhub-taskpackage.md)"
fi

mkdir -p "$OUT_DIR/artifacts"
STDOUT_PATH="$OUT_DIR/aigdbench.stdout"
STDERR_PATH="$OUT_DIR/aigdbench.stderr"
REPORT_PATH="$OUT_DIR/aigdbench.report.json"

CMD=("${AIGDBENCH[@]}" run
     --testcases-dir "$TESTCASES_DIR"
     --testcase "$TESTCASE"
     --driver "$DRIVER"
     --godot-binary "$GODOT_BIN"
     --report "$REPORT_PATH"
     --artifacts-dir "$OUT_DIR/artifacts")

if [ "$DRIVER" = "patch" ]; then
    PATCH="$TESTCASES_DIR/$TESTCASE/fix.diff"
    [ -r "$PATCH" ] || fail_config "patch driver needs $PATCH (missing for testcase '$TESTCASE')"
    CMD+=(--patch "$PATCH")
fi

printf '[aigdbench-task] cwd=%q running:' "$REPO_ROOT" >&2
printf ' %q' "${CMD[@]}" >&2
printf '\n' >&2

# Run as a child (not `exec`) so the exit code can be captured, the report
# translated into result.json, and the EXIT trap still fire. `set -e` is
# disabled around the call so a non-zero scorer exit is captured (not turned
# into an abort) and re-raised verbatim below -- the scorer's own exit code
# is always the authoritative verdict, never masked.
set +e
"${CMD[@]}" >"$STDOUT_PATH" 2>"$STDERR_PATH"
rc=$?
set -e

if ! python3 - "$REPORT_PATH" "$OUT_DIR/result.json" "$TESTCASE" "$DRIVER" "$rc" \
    "/out/aigdbench.stdout" "/out/aigdbench.stderr" <<'PY'
import json, sys

report_path, out_path, testcase, driver, rc, stdout_path, stderr_path = sys.argv[1:]
rc = int(rc)

mean = None
checks = []
try:
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    mean = report.get("mean_score")
    testcases = report.get("testcases") or []
    if testcases:
        vr = testcases[0].get("verifier_result") or {}
        for c in vr.get("checks", []):
            checks.append({
                "name": c.get("name", ""),
                "passed": bool(c.get("passed")),
                "detail": c.get("detail", "") or "",
            })
except Exception:
    pass  # a missing/malformed report degrades to mean_score=null, not a crash

result = {
    "testcase": testcase,
    "driver": driver,
    "status": "success" if rc == 0 else "failed",
    "exit_code": rc,
    "mean_score": mean,
    "checks": checks,
    "stdout_path": stdout_path,
    "stderr_path": stderr_path,
}
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
then
    # A result-writer failure is itself a CONFIG_ERROR -- but only when the
    # scorer otherwise succeeded; never mask a real scorer failure exit code.
    [ "$rc" -eq 0 ] && fail_config "failed to write $OUT_DIR/result.json from the scorer's report"
    echo "aigdbench-task: warning: could not write result.json (scorer exit $rc still propagated); see $STDOUT_PATH / $STDERR_PATH" >&2
fi

exit "$rc"
