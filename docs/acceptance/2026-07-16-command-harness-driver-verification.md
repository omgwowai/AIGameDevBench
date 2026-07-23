# `feat/command-harness-driver` — Functional Review + BeaverHub Live Verification

**Date:** 2026-07-16
**Branch reviewed:** `feat/command-harness-driver`
**Scope:** functional inventory, real (non-simulated) verification within an
operator-authorized cost/security boundary, and cross-repo issue backlog.

**Conclusion:** Systematic verification within this round's authorized cost
and security boundary is complete. This is **not** a full-product
acceptance — a full-scale (30-case) benchmark, a real paid AI harness run,
the webhook → auto-merge/release chain, and part of the dashboard's write
path remain unexecuted (§8), each for a specific cost/credential/side-effect
reason recorded below. High-cost, real-credential, and high-side-effect live
legs stay operator-gated by design; the three AIGameDevBench PRs from this
round (§9) are **not merged**.

---

## 1. Feature map

**CLI (`aigdbench`, 8 subcommands):** `list` (testcase enumeration) ·
`run` (core scoring: `--driver noop|patch|command`, `--jobs` concurrency,
`--repeat` resampling, `--timeout`/`--stall-timeout`, `--report`/`--log-dir`/
`--artifacts-dir`, `--harness-format`, an experiment mode via
`--experiment YAML`) · `audit` (per-case noop=0/golden=1 health gate) ·
`smoke` (single-case audit) · `compare` (paired-bootstrap significance) ·
`probe-permissions` (filesystem-effect-observing sandbox probe) ·
`scaffold` (new-testcase bootstrap) · `serve` (dashboard).

**Drivers:** `NoOpDriver` / `PatchDriver` / **`CommandHarnessDriver`** (this
branch's centerpiece — shell-injection-free template substitution for
`{task}/{task_file}/{workspace}`, three-layer guard against timeout/stall/
approval-block, stream-json event parsing for turns/tokens/slowest-turn).
Plus Codex `CODEX_HOME` materialization for component-level ablation
experiments.

**Scoring pipeline:** isolated workspace → git-diff capture → L0/L1
admission gate (syntax + scene-load + resource/signal integrity) → one of 8
verifier types (3 pure-Python, 4 requiring a Godot binary, 1 survey-history
regression) → `failure_stage` classification
(`no_change/harness_error/l0/l1/verifier`).

**Dashboard (`serve`):** Reports/Testcases/Contents/Run/Status/Webhooks
tabs; read-only by default, `--editable` opens the testcase editor; the Run
tab is single-flight (409 while busy).

**Orchestration scripts:** `run_k8s_matrix.sh` (one Job per testcase,
concurrency-gated, results transported via pod-log markers) ·
`run_local_matrix.sh` (docker/podman local equivalent) ·
`bench-orchestrator.sh` + `webhook_receiver.py` (PR-opened → candidate flow)
· `bench-candidate.sh` (cherry-pick → immutable `:<sha8>` image → benchmark
→ merge only if strictly above the historical best score) ·
`build_runner_image.sh` / `aggregate_report.py` / `start_dashboard.sh`.

**Delta vs. `main`:** `main` already carries an earlier harness-driver merge
(upstream PRs #3–#5); this branch leads by 40+ commits, the bulk being the
full dashboard (Run/Status/Webhooks/image dropdown), webhook candidate flow
v2, the `beaver_hub-public` registry path, per-turn timing, and
concurrency/resampling with significance testing.

**Explicitly absent:** resume / failed-only rerun / mid-run cancel at the
CLI layer. No `.beaver/tasks/aigdbench` TaskPackage lives in this repo — the
BeaverHub-side binding is external (§2).

---

## 2. Dependency relationship: AIGameDevBench ↔ BeaverHub

**Loosely coupled, opposite direction:**

- AIGameDevBench → BeaverHub: **zero code dependency.** The only shared
  surface is the Harbor project name `beaver_hub-public` (accessed via
  direct `kubectl` + Harbor v2 API, not through the `beaver` CLI).
- BeaverHub → AIGameDevBench: the catalog registers a `beaver-godot` beaver
  (task `aigdbench`, digest-pinned v1, `source_repo: omgwowai/AIGameDevBench`)
  that wraps this repo's scorer as a one-shot, zero-secret,
  offline-scoring image dispatchable via `beaver image run`.

**Consistency gaps found and filed** (not fixed — cross-repo/contract
decisions, see §10):

- The catalog's NO-BAKE design (testcases read from a PVC) does not match
  the currently-bound image's actual behavior (reads a baked-in path
  instead) — reproduced fresh against the same digest a prior, closed
  BeaverHub issue reported; likely an unpublished fix rather than a new
  regression (BeaverHub issue, commented on the original report).
- AIGameDevBench's own dashboard/CLI default runner-image tag doesn't exist
  in the registry (AIGameDevBench issue, filed fresh).

---

## 3. Test matrix

| Capability | Entry point | Requires | Offline? | Expected | **Actual** |
|---|---|---|---|---|---|
| noop baseline | local CLI | — | yes | 0.00 | 0.00 (`no_change`) |
| golden patch | local CLI | — | yes | 1.00 | 1.00 |
| partial score | local CLI, bad diff | — | yes | 0.50 | 0.50 (`partial`) |
| command driver + report/artifacts | local CLI | fake harness | yes | 1.00 + artifacts | 1.00, diff+files+CI95+per-stage timing all present |
| failure: unknown testcase / missing `--harness-cmd` | local CLI | — | yes | clear error | clear error, clean exit |
| failure: nonzero exit / timeout / missing binary | local CLI | — | yes | honest classification | correctly classified `harness_error`, timeout killed at bound, no crash |
| batch + concurrency (`-j2`) + aggregate | local CLI | — | yes | aggregated | mean over 3 cases correctly aggregated |
| audit / smoke | local CLI | — | yes | healthy | 3/3, 1/1 healthy |
| compare (significance) | local CLI | two reports | yes | CI + p-value | significant result correctly computed |
| probe-permissions | local CLI | compliant/non-compliant harness | yes | PASS/FAIL as appropriate | both directions correct |
| unit test suite | pytest | — | yes | all pass | 226 passed, 1 skipped (re-verified post-rebase, see §9) |
| dashboard tabs + API | `serve` | — | yes (viewing) | render + graceful degradation | confirmed; image-list endpoint degrades gracefully with no cluster access |
| **BeaverHub real submit (patch)** | operator-side `beaver image run --confirm --wait --collect` | doctor green | no | 1.00 | **1.00**, all checkpoints passed |
| BeaverHub noop control | same | same | no | 0.00 | **0.00** |
| BeaverHub dry-run | `beaver image run --dry-run` | profile | yes | rendered manifest | rendered correctly |
| BeaverHub resource cleanup | `beaver run cleanup --confirm` | — | no | targeted deletion | Job + input ConfigMap deleted, no leftovers |
| Harbor tag discovery | operator credentials → Harbor v2 | credentials | no | tag list | 4 immutable tags found; **no `:latest`** |
| repo's own k8s matrix script | `run_k8s_matrix.sh` | kubectl | no | 2/2 = 1.00 | failed → fixed 3 bugs → **2/2 = 1.00** (full RED/GREEN history in AIGameDevBench PR #6) |
| off-server `beaver use` real submit | npm CLI | — | no | submit | blocked by an over-broad client-side readiness gate (BeaverHub issue filed) |
| `run submit --server` | dispatch | — | no | submit | dispatch input schema can't carry task-specific fields (commented on existing BeaverHub roadmap issue) |

**Not executed this round** (see §8 for reasons): full 30-case benchmark;
real paid AI harness on-cluster; webhook → candidate → auto-merge/release
chain; Godot-runtime verifiers run directly on this machine (no local Godot
binary — indirectly exercised via the cluster image, whose scoring pipeline
does include a `godot_scene_assert` case); dashboard `--editable` write
path.

---

## 4. Commands actually run (representative, not exhaustive)

Local: `uv sync --extra dev` → `pytest -q` → `aigdbench list/run(×9 variants)/
audit/smoke/compare/probe-permissions/serve` + dashboard API smoke checks.

Operator-side (BeaverHub-enrolled host, reached only via the operator's own
SSH access — no kubeconfig or secret value ever touched this machine):
`beaver doctor` → `beaver image run beaver_hub-public/beaver-godot --input
<file> --profile-dir <dir> --dry-run/--confirm --wait --collect` (×3,
patch/noop/control) → `kubectl get/logs` for evidence → `beaver run cleanup
--confirm` (×3) → `scripts/run_k8s_matrix.sh` (×4, across the three-round
fix/verify cycle for AIGameDevBench PR #6).

---

## 5. Successes and failures worth recording

**Successes:** BeaverHub patch=1.00 / noop=0.00 both confirmed on a live
Job; `run_k8s_matrix.sh` fixed to 2/2=1.00 through a genuine RED→GREEN
cycle; full local test matrix green.

**Failures with diagnostic value:**
- An unexpanded `${BEAVER_K8S_NAMESPACE}` placeholder was sent literally to
  the Kubernetes API, producing a misleading `403` instead of a client-side
  config error (BeaverHub-side gap, commented on an existing tracked issue
  covering exactly this).
- A `--wait --collect` run reported `mean_score: null, stdout_captured:
  false` for a Job that had, in fact, succeeded and printed a complete
  result — a log-follow race, not a real scoring failure (new BeaverHub
  issue filed).
- The first `run_k8s_matrix.sh` attempt against this cluster reported
  `no report in pod log` for testcases that had, in fact, scored 1.00 — a
  CRI log-format + log-fetch-timing issue in this repo's own script, fixed
  and verified (AIGameDevBench PR #6).

---

## 6. Cluster evidence (redacted to non-replayable summary form)

- **Namespace:** the operator's own benchmark namespace (logical scope
  only; no literal name recorded here).
- **Placement:** pod scheduled onto the node pool the profile's
  `nodeSelector` requested (constraint honored).
- **Image:** matched the catalog's pinned v1 digest for `beaver-godot`
  (short prefix only: `sha256:8d67867…`).
- **PVC:** the shared testcase-corpus PVC mounted read-only at the
  contract-specified path; mount succeeded.
- **Scores:** `patch` → **1.00** (4/4 checkpoints passed); `noop` control →
  **0.00** (all 4 checkpoints correctly failed, confirming the scorer isn't
  vacuously passing).
- **Terminal state / exit code:** `succeeded` / `0` in both runs.
- **Cleanup:** every managed Job + input ConfigMap created during
  verification was deleted via `beaver run cleanup --confirm`; a
  post-cleanup label-selector query returned no leftover resources.
- **Local machine posture:** no kubeconfig, no Secret value, and no private
  key content ever resided on the machine that ran this review — all
  cluster/Harbor credential resolution happened server-side, by name, on
  the operator-controlled enrolled host.

Full, non-redacted run records (including whatever run IDs and pod names
the operator's own `.beaverhub/runs/` directory retains) exist only in that
controlled location and are not reproduced here.

---

## 7. Shortest reliable npm path

```text
Can you use it right after `npm install`: needs one operator configuration
step (and a real submit currently still requires reaching an enrolled
server — it doesn't complete end-to-end from a bare laptop yet).

Minimum steps:
1. Configure ~/.npmrc for the private GitHub Packages scope, then
   `npm i -g @omgwowai/beaverhub@alpha`.
2. Add the BeaverServer's SSH Host entry to ~/.ssh/config (operator-supplied).
3. `printf %s <ssh-host-alias> | beaver config set-server`
4. `BEAVERHUB_SERVER_PROBE=1 beaver doctor`  → AccessMode should PASS.
5. `beaver use beaver-godot aigdbench --dry-run --profile-dir <profiles>`
   → offline render succeeds.
6. Real submit currently requires SSH'ing onto the enrolled server and
   running `beaver image run ... --confirm --wait --collect` there directly
   — the off-server one-command path is blocked by two filed BeaverHub
   gaps (over-broad readiness gate; dispatch input schema can't carry
   task-specific fields).

Still-missing out-of-box experience:
- The profile template previously had to be manually copied out of the
  BeaverHub source tree (fixed this round — AIGameDevBench PR #8 ships an
  owning-repo copy, fully placeholder-generic, not merged yet).
- Off-server `beaver use`/`run submit --server` cannot complete a real
  aigdbench submission in one command (two BeaverHub-side gaps, filed/
  commented this round — not fixed, cross-repo backlog).
```

---

## 8. Not executed this round, and why

| Item | Reason held back |
|---|---|
| Full 30-case benchmark | explicit cost-scope instruction — minimal-then-scale was authorized, full matrix was not |
| Real paid AI harness on-cluster | real model-API cost; explicit operator-gated live leg |
| webhook → candidate → auto-merge/release chain | real PR webhook source + plugin-repo write access + an actual merge/release side effect |
| dashboard write/submit path (Run tab real submit from this machine) | this machine intentionally holds no kubeconfig; exercising it would mean provisioning one, out of scope this round |
| new-secret-requiring experiments | explicit instruction: no new Secret creation this round |

---

## 9. PR status (AIGameDevBench, fork `lixuan-shi/AIGameDevBench` → base
`feat/command-harness-driver`)

All three re-verified **after** confirming each branch was already even
with upstream `feat/command-harness-driver` HEAD (`70c6100` at time of
writing — 0 commits behind on all three, so no rebase conflicts to
resolve). Re-verification performed regardless of the "no conflict" state,
per this round's requirement: full `pytest -q` (226 passed, 1 skipped on
each), `bash -n` on the modified script, the CRI-prefix/clean-log dual-input
extraction check, profile-template materialization + YAML validation, and a
secret/sensitive-content scan of each branch's diff against upstream (all
clean). `scripts/run_k8s_matrix.sh` itself was unchanged by the rebase (it
was already current), so the existing three-round real-cluster RED→GREEN
evidence was retained rather than re-run; that evidence is tied to head SHA
`01251e34afce12eee62711569f38bbe5d1f8948b`, produced against the same code
this SHA still points to.

| PR | Title | Status | Head SHA |
|---|---|---|---|
| [#6](https://github.com/omgwowai/AIGameDevBench/pull/6) | `run_k8s_matrix.sh`: kubectl-only host + CRI log prefix + log-fetch race fixes | `awaiting-maintainer-review` | `01251e34afce12eee62711569f38bbe5d1f8948b` |
| [#7](https://github.com/omgwowai/AIGameDevBench/pull/7) | Remove accidentally committed `.claude/worktrees` gitlinks | `awaiting-maintainer-review` | `5ab040c304a8cbdd258deb599c151895274a656c` |
| [#8](https://github.com/omgwowai/AIGameDevBench/pull/8) | Document the `beaver-godot` binding + ship a fully-generic profile template | `awaiting-maintainer-review` | `710be2a089a1251cee6045bb9bf35ccc16a1c758` |

All three: no upstream write access on this account (confirmed — a direct
push attempt returned 403), so a one-time review request was left as a PR
comment on each (not a reviewer-API call, which is also permission-gated).
**None of the three has been merged, and none should be merged this
round** — they are being kept in a directly-mergeable state
(`awaiting-maintainer-review`) pending an upstream maintainer's own
decision on timing.

---

## 10. Issues filed / commented this round (cross-repo backlog)

Every item below was checked against existing open **and closed** issues
first; genuine duplicates were commented on with fresh evidence instead of
re-filed.

**BeaverHub (`omgwowai/BeaverHub`, private):**

| # | Type | Title / topic |
|---|---|---|
| [#586](https://github.com/omgwowai/BeaverHub/issues/586) | comment | Namespace placeholder (not just `context`) still reaches the K8s API unexpanded — closed issue's gap #3 reproduced fresh |
| [#215](https://github.com/omgwowai/BeaverHub/issues/215) | comment | Cross-reference: same bug class as the `context`-scoped fix, for the `namespace` field |
| [#771](https://github.com/omgwowai/BeaverHub/issues/771) | comment | NO-BAKE PVC-read regression/unpublished-fix evidence — same digest, same symptom, after the issue's closure |
| [#890](https://github.com/omgwowai/BeaverHub/issues/890) | comment | Concrete TPK-5 (Task Materializer) blocker: dispatch input schema can't carry `aigdbench`'s `{driver, testcase}` |
| [#1129](https://github.com/omgwowai/BeaverHub/issues/1129) | new issue | `image run --wait --collect` log-follow race loses a successful Job's result |
| [#1130](https://github.com/omgwowai/BeaverHub/issues/1130) | new issue | Doctor-gated invocations check unrelated capabilities' dependencies instead of the invoked capability's own declared scope |

**AIGameDevBench (`omgwowai/AIGameDevBench`):**

| # | Type | Title / topic |
|---|---|---|
| [#9](https://github.com/omgwowai/AIGameDevBench/issues/9) | new issue | Default runner-image tag `:latest` doesn't exist in the Harbor project — options presented, no decision made |

None of the above were implemented this round (per this round's scope:
polish, file, document — not build). Each issue lists concrete requirements
without prescribing a specific implementation, particularly the doctor-gate
scoping issue (#1130), which is deliberately framed around
capability-declared facts + a fail-closed invocation gate rather than any
specific bypass mechanism.

---

## 11. What this document deliberately omits

Per this round's value-free documentation requirement: no node IPs, no
bastion/login host addresses, no private-key fingerprints, no Secret
contents (names only, where a name itself needed mentioning — none did in
this document), no directly-replayable credential configuration, and no
full Job/Pod UIDs or internal absolute file paths. Where cluster evidence
was needed, it appears here only as: workload outcome (score, exit code,
terminal state), placement constraint satisfaction (pool honored — no pool
name recorded), image identity (digest short-prefix only), and cleanup
confirmation. The full, non-redacted evidence remains in the operator's own
controlled run-record store and was not copied into this repo.
