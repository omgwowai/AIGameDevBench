# aigdbench — BeaverHub TaskPackage

Runs one AIGameDevBench testcase as a BeaverHub task: one testcase in
(`/in/input.json`), one structured result out (`/out/result.json`). This makes
explicit the contract that `docker/entrypoint.sh` carries implicitly, so the
same runtime + contract is reusable across BeaverHub RunTypes instead of the
hand-written scheduling/log-scraping/cleanup in `scripts/run_k8s_matrix.sh`.

## Files

| File | Purpose |
|---|---|
| `task.yaml` | The TaskPackage contract (`beaverhub.dev/task/v1`). Self-contained inline entrypoint. |
| `input.schema.json` | Input contract: `{testcase, driver, harness_cmd?, testcases_dir?, engine?, timeout_seconds?}`. |
| `batch.filtered-patch.json` | A `BatchPlan` fanning the whole `testcases_filtered/` set (37 items) over the `patch` driver. |
| `run.sh` | Reference wrapper (env→argv). **Not used by `task.yaml`** anymore (see "Why the entrypoint is inlined"). Kept as the readable, proven-equivalent form of the entrypoint logic. |

## Verified status

- `beaver task validate` → OK; `beaver task conformance` → **PASS (10 checks)**.
- `beaver batch validate` → OK (37 items); `beaver batch conformance` → **PASS (8 checks)**.
- End-to-end on a real cluster: `beaver task run … --wait --confirm` on client
  **0.0.2-alpha.1** against local **k3s** ran the testcase
  `pathfinding-npc-bridge-astar` with `driver=patch` (golden fix) to
  **mean score 1.000** (all 3 checkpoints PASS), `run submit: exit=0`,
  result-manifest closure SUCCESS, pod auto-cleaned.

## Why the entrypoint is inlined

`beaver task run` does **not** mount the owning repo into the container. An
entrypoint of `bash .beaver/tasks/aigdbench/run.sh` therefore fails with
`exit 127` (script not found). The contract instead inlines the logic as a
`bash -lc <script>` argv that depends only on what the image already ships
(`aigdbench` CLI + baked `testcases_filtered/`). `bash` is `argv[0]`, so this is
a valid argv, not a taskspec-refused bare shell string. `run.sh` is retained as
the human-readable equivalent (and the earlier proof that it produces
byte-identical results to `docker/entrypoint.sh`).

## Run it

```bash
# single testcase (golden patch)
echo '{"driver":"patch","testcase":"pathfinding-npc-bridge-astar","testcases_dir":"/app/testcases_filtered"}' > in.json
beaver task run .beaver/tasks/aigdbench/task.yaml \
  --input in.json --profile beaver-godot --profile-dir <profiles> --wait --confirm

# whole filtered set
beaver batch run .beaver/tasks/aigdbench/batch.filtered-patch.json --confirm
```

`driver`: `noop` (baseline, expect 0.00) · `patch` (golden fix, expect 1.00;
the wrapper resolves `fix.diff`/`good.diff`) · `command` (real AI harness,
requires `input.harness_cmd`, e.g. `claude -p {task} --dangerously-skip-permissions`).

## Environment prerequisites (learned bringing this up on a self-hosted node)

The contract itself is clean; these are **cluster/deployment** requirements, not
task issues. On a self-hosted BeaverNode over k3s we hit and resolved, in order:

1. **Client version.** Use **0.0.2-alpha.1** (pinned — do not chase floating
   alpha/beta tags). It fixes #1130: the doctor's hardcoded default private
   space (`beaver_hub-u-daodao`) no longer blocks a public-space task like
   `beaver-godot`. On alpha.15 every `task run`/`use` was refused by that gate.
2. **Image must be pullable by the cluster.** The `beaver_hub-public` registry
   project was empty (`tags: []`); the runner image existed only in local Docker.
   For k3s (containerd, not Docker): `docker save … | sudo k3s ctr images import`,
   and — because the profile pins by digest — also tag the digest name so
   `IfNotPresent` hits locally:
   `sudo k3s ctr images tag <repo>:latest <repo>@sha256:<digest>`.
   The profile `container.image` must be **digest-pinned** (`@sha256:…`), not `:latest`.
3. **Credential secrets must exist.** The contract declares
   `credentials: [ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN]` (for the `command`
   driver). BeaverHub renders them as `secretKeyRef`s, so even a `patch` run
   needs the K8s secrets to exist or the pod fails `CreateContainerConfigError`:
   `kubectl create secret generic beaver-cred-anthropic-auth-token --from-literal=ANTHROPIC_AUTH_TOKEN=<value>`
   (and `…-base-url`). For `patch`/`noop` the values are unused (placeholders fine).
4. **Local (no-node) submit.** With a BeaverNode enrolled, `task run` dispatches
   over SSH. To run against the local cluster directly, clear the node
   enrollment (`beaver config unset-node`) so `config.json` `mode` is `local`.

## Known platform gaps observed (reported upstream)

- **#1130** — `task run`/`use` gates lack the capability-scope convergence that
  #1141 gave the `image` verbs; the hardcoded daodao default space is probed for
  unrelated invocations. Fixed for the public-space case in 0.0.2-alpha.1.
- **#1466** — `image run --server` gate ignored `BEAVERHUB_SERVER_PROBE`; the
  fix ships dark in 0.0.2-alpha.1 (old Go dispatch path is still the default).
- **Result retrieval on a PVC-less node.** `task run` has no `--json`/`--timeout`
  (only `--wait`/`--no-wait`/`--node`), and `/out` is an `emptyDir` read back by
  the platform, not written to the host. Profile `workspace` supports only
  `ephemeral`/`pvc-existing`/`pvc-provisioned` — **no hostPath** (ADR-0003). So
  on a node without a shared PVC, the structured result is captured by reading
  the pod's logs during the run (the entrypoint `tee`s aigdbench output; the CLI
  itself only returns an exit code). On a real cluster, mount a PVC at `/out`.
