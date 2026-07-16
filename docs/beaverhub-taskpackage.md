# The `aigdbench` BeaverHub TaskPackage

This document describes `.beaver/tasks/aigdbench/`: this repo's own copy of
its BeaverHub execution contract, per
[ADR-0068](https://github.com/omgwowai/BeaverHub/blob/main/docs/adr/0068-taskpackage-and-runtime-only-beavers.md)
(`TaskPackage v1 + Runtime-Only Beavers`) in the `omgwowai/BeaverHub` repo.

It is a **separate file from [`docs/beaverhub.md`](beaverhub.md)** (added by
BeaverHub-integration PR #8, not yet merged as of this writing) rather than an
edit to it, precisely so this change does not depend on, duplicate, or
conflict with that open PR. Once #8 lands, the two documents should be
cross-linked or merged; that is a follow-up for whoever lands #8, not this
change.

## Vocabulary (from ADR-0068, BeaverHub repo)

> "A Beaver carries no business semantics, a TaskPackage carries no runtime
> detail."

| Term | Owner | What it is here |
|---|---|---|
| **Beaver** (`beaver-godot`) | BeaverHub | A digest-pinned runtime: headless Godot + OS deps. Declares no business task. |
| **TaskPackage** (`.beaver/tasks/aigdbench/`) | **this repo** | The business-owned execution unit: `task.yaml` + `run.sh` + input/result schemas + the domain semantics (driver, testcase, mean_score). |

Before this change, the business half of that binding (which testcase to run,
what `driver=noop|patch` means, how a score is computed) lived only in
BeaverHub's `images/beaver-godot/entrypoint.sh` and `images/beavers.yaml` —
the opposite of where ADR-0068 says it belongs. `.beaver/tasks/aigdbench/`
moves that half here.

## Files

```text
.beaver/tasks/aigdbench/
  task.yaml            TaskPackageV1 declaration (BeaverHub TPK-3 shape)
  input.schema.json     {driver: noop|patch, testcase?: <id>}
  result.schema.json     {testcase, driver, status, exit_code, mean_score, checks, stdout_path, stderr_path}
  run.sh                 /in -> `aigdbench run` -> /out bridge (this repo's own entrypoint)
```

`task.yaml`'s shape mirrors BeaverHub's own conformance fixtures
(`examples/task-minimal`, `task-with-credentials`, `task-repo-test`,
`task-with-artifacts` in the BeaverHub repo) field for field: `apiVersion:
beaverhub.dev/task/v1`, `kind: TaskPackage`, an argv entrypoint (never a
shell string), repo-relative schema paths, `/out`-rooted output paths, and a
duration timeout.

## Input / output contract (spec/v1)

Same wire shape as BeaverHub's current `beaver-godot` binding
(`images/beaver-godot/description.md` `## Contract` in the BeaverHub repo):

```json
{ "driver": "noop" | "patch", "testcase"?: "<id>" }
```

- `driver` (required): `noop` scores the unmodified baseline (contract:
  `mean_score` 0.00); `patch` applies the testcase's own `fix.diff` golden fix
  (contract: `mean_score` 1.00 for a healthy testcase).
- `testcase` (optional): a bare id of a `testcases/<id>/` directory in *this*
  repo. Defaults to `collision-layer-precise-edit` — see "Why this default"
  below.

`run.sh` writes `/out/result.json`:

```json
{
  "testcase": "collision-layer-precise-edit",
  "driver": "patch",
  "status": "success",
  "exit_code": 0,
  "mean_score": 1.0,
  "checks"?: [{"name": "...", "passed": true, "detail": "..."}],
  "stdout_path": "/out/aigdbench.stdout",
  "stderr_path": "/out/aigdbench.stderr"
}
```

`status`/`exit_code` are derived **only** from the `aigdbench` CLI process's
own exit code — never from `mean_score` — so a correctly-measured
`noop`/`mean_score: 0.0` run is `status: "success"` (a successful
*measurement*, not a scorer failure), matching BeaverHub's ADR-0022
exit-class discipline. A malformed/absent `/in/input.json`, or an
unresolvable `testcase`, is a `CONFIG_ERROR` (**exit 2**) caught before the
scorer runs at all.

`run.sh` also writes `/out/artifacts/<testcase>/` (the harness's diff plus a
copy of every changed file), via `aigdbench run --artifacts-dir`.

## Why `collision-layer-precise-edit` is the default (not `tetris-clear-scores`)

BeaverHub's `images/beaver-godot/description.md` and its
`docs/beaverhub/profile-beaver-godot.example.yaml` / `smoke-input.json`
default to a testcase named `tetris-clear-scores`. **That testcase does not
exist anywhere in this repo's `testcases/` corpus** (verified: `find . -iname
'*tetris*'` finds nothing) — it appears to only ever have existed as
PVC-hosted data alongside BeaverHub's now-deprecating `beaver-godot` image, or
was renamed/removed since. Rather than hardcode a default this repo cannot
itself satisfy, this TaskPackage defaults to a testcase that:

1. actually exists in this repo's own `testcases/` corpus,
2. is fully self-contained (folder-type, no external repo, no shared
   snapshot), and
3. was **verified end-to-end with a real headless Godot 4.6.2 binary** as
   part of landing this change: `noop` scored `0.00`, `patch` scored `1.00`
   (see the PR description / commit for the exact commands run).

`collision-layer-precise-edit` met all three. Several of this repo's own
`source_repo = "authored:representative-set"` testcases did **not** pass this
check even after normalizing line endings (their committed `fix.diff` does
not `git apply` cleanly against their own committed `baseline/`) —
`damage-formula-refactor`, `ability-cooldown-gate`, `component-layer-boundary`,
`event-bus-priority-dispatch`, `health-damage-death`, and
`inventory-equipment-system` among them (run `aigdbench audit --testcases-dir
testcases --only <id> --godot-binary <path>` to reproduce). That is a
pre-existing testcase-corpus health question, **out of scope for this
change** (non-goal: this PR does not modify `testcases/`) — but it is worth a
maintainer's separate look, since `aigdbench audit` exists precisely to catch
this and currently is not run in CI against the full corpus.

## Consumer quick-start (BeaverHub side)

From an environment with the `beaver` CLI and cluster access (see BeaverHub's
own `docs/guides/run-a-task.md`):

```bash
beaver task validate .beaver/tasks/aigdbench/task.yaml
beaver task render --profile <profile.yaml> .beaver/tasks/aigdbench/task.yaml
beaver task run --confirm --input '{"driver":"patch","testcase":"collision-layer-precise-edit"}' \
  .beaver/tasks/aigdbench/task.yaml
```

These three verbs are documented from BeaverHub's own TaskPackage materializer
design (ADR-0068 §3: "the materializer is client-side... TaskPackage + Profile
+ Input project onto the existing `/v1` submit request"); **this PR does not
execute them** — this repo has no `beaver` CLI / cluster access, and any
BeaverHub-side change is out of scope for this track. The one thing exercised
directly, repeatedly, and with a real Godot engine in this repo's own
sandbox is `run.sh` itself (see "Verification" below).

## Known gaps

- **No `network.egress` capability, so `run.sh` does not install its own
  Python dependencies.** `task.yaml` declares `python3`, `godot`, and `git` as
  runtime binaries, but this repo's actual harness dependencies (`click`,
  `godot-parser`, `gdtoolkit`, `networkx`, `pyyaml`/`tomli`, and the
  `aigamedevbench` package itself) are not among them — declaring arbitrary
  Python packages as generic runtime binaries doesn't fit ADR-0068's
  `binaries: {name: version-constraint}` shape, and installing them over the
  network at run time would require a capability this offline, no-secret
  TaskPackage deliberately does not request. `run.sh` therefore **requires**
  the runtime image to already have run an equivalent of `pip install .`
  against this repo (mirroring what BeaverHub's own now-deprecating
  `beaver-godot` Dockerfile did at *build* time) — if neither the `aigdbench`
  console script nor the importable `aigamedevbench` package is present,
  `run.sh` fails with a clear `CONFIG_ERROR`, not a confusing traceback. A
  generic `godot` runtime image that pre-bakes this repo's engine deps (the
  BeaverHub-side half of ADR-0068 §2's runtime convergence) would close this
  gap; building that image is BeaverHub-side work and out of scope here.
- **No `beaver task conformance` run.** This sandbox has no BeaverHub Go
  toolchain and no cluster access (this track's stated non-goal). `beaver
  task conformance` (BeaverHub's authoritative TaskPackageV1 validator) was
  **not** run against `task.yaml`. Instead, [`tests/test_beaverhub_taskpackage.py`](../tests/test_beaverhub_taskpackage.py)
  re-derives the same fail-closed rules described in BeaverHub's
  `internal/taskspec/taskpackage_validate.go` (apiVersion/kind, a
  slug-shaped `metadata.name`, non-empty argv, repo-relative `input.schema`,
  `/out`-rooted `output.result`/`output.artifacts`, bare-NAME-only
  `credentials`, a positive Go-duration-shaped `timeout`) directly against
  this repo's `task.yaml`, plus validates `input.schema.json` /
  `result.schema.json` are well-formed JSON Schema documents whose
  `required`/`enum`/`additionalProperties` rules actually accept/reject the
  fixtures this doc claims they do. This is **not** a substitute for running
  the real Go validator — it is a same-rules re-implementation in Python,
  and is described here so no one mistakes it for BeaverHub's own
  conformance run.
- **No `pr-review-action`/`split-action`-style "zero Ring-0 diff" proof.**
  ADR-0068 §7's release gate (two business repos onboard end to end with a
  BeaverHub diff of zero) is a BeaverHub-side / cross-repo concern; this PR
  only delivers this repo's own half.

## Parity vs. the retired `aigdbench` CI-provider (`npm/src/ci/providers/aigdbench.ts`, BeaverHub repo)

BeaverHub's TPK-2 migration ledger (`docs/migrations/business-content-relocation.md`,
Group A) names `npm/src/ci/providers/aigdbench.ts` (+ its fixtures) as the
domain half of a benchmark-CI provider adapter that is supposed to migrate
here. **This PR does not implement that provider or its GitHub Actions
template (TPK-6 in that ledger's own wording).** What this PR verified vs.
what it did not:

| Claim | Status |
|---|---|
| Same input shape (`{driver, testcase}`) | **Yes** — `input.schema.json` matches the beaver-godot binding's documented contract exactly. |
| Same result shape (`mean_score`, `status`, `exit_code`) | **Yes**, plus a `checks` array the old image's `entrypoint.sh`-derived contract did not carry (this repo's own CLI already reports per-checkpoint detail; `run.sh` surfaces it rather than discarding it). |
| Same `noop=0.00`/`patch=1.00` smoke-check pair | **Yes, verified for real** against a testcase in this repo (`collision-layer-precise-edit`, not the old default — see above). |
| Same CI-provider preset defaults, dispatch-request shape, generated-workflow parity vs. `agent-game-dev-aigdbench.workflow.golden.yml` | **Not verified. Not claimed.** This PR adds no GitHub Actions template and does not touch `npm/src/ci/providers/aigdbench.ts` (that file lives in the BeaverHub repo, out of scope here). Anyone advancing the TPK-2 ledger row for that file needs a **separate** change that actually builds and proves the CI-provider/workflow-template parity — this PR's schemas and `run.sh` are necessary groundwork for that, not a substitute for it. |

## Verification

Local, offline, in this repo's own sandbox (see the PR body for the exact
commands and their full output):

- `uv sync --extra dev && uv run pytest tests/ -q` — **254 passed** (this
  repo's pre-existing 226-passed baseline, unaffected since nothing under
  `src/` was modified, plus 28 new cases from
  `tests/test_beaverhub_taskpackage.py` below), **2 skipped** without a
  local Godot binary on `PATH`/`AIGDBENCH_TEST_GODOT_BINARY` (the
  pre-existing baseline's 1 skip, plus this suite's own
  `test_real_noop_and_patch_scoring_when_godot_available`) — **255 passed,
  1 skipped** when one is available.
- `tests/test_beaverhub_taskpackage.py` — schema-shape validation of
  `task.yaml`/`input.schema.json`/`result.schema.json` (see "Known gaps"
  above for exactly what this does and does not prove), plus an offline
  consumer smoke that invokes `run.sh`'s own `CONFIG_ERROR` paths (bad
  driver, unknown testcase, path-traversal testcase id, malformed JSON,
  unknown input key, missing input file) without needing Godot at all.
- A **real, end-to-end** run of `run.sh` against a locally available headless
  Godot 4.6.2 binary (not part of the automated test — a real Godot engine
  is not assumed present in every environment that runs `pytest`): `driver:
  noop` scored `mean_score: 0.0`, `driver: patch` (with
  `testcases/collision-layer-precise-edit/fix.diff`) scored `mean_score: 1.0`,
  both `status: "success"`, `exit_code: 0`. This is the same
  noop=0/patch=1 contract BeaverHub's `beaver-godot` binding already claims,
  now reproduced entirely from this repo's own `.beaver/tasks/aigdbench/`
  entrypoint.
