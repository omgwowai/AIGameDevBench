# PR Webhook → 并行 Benchmark → 门禁 → 自动合并/发布 工作流规格

本文描述从收到 GitHub `pull_request` webhook 到（可选）自动合并 PR + 发布插件新版本的完整链路：
每一步做什么、调用哪个镜像、结果存在哪里、门禁如何判定、以及哪些信息记进版本发布日志。

> **触发口径**：**仅 `pull_request` 且 `action=opened`**。`synchronize`/`reopened`/
> `ready_for_review` 以及所有 `push` 事件都不触发（编排器回 `202 {skipped}` 忽略）。
> 权威设计见 [`spec-v2.md`](spec-v2.md)。

涉及的组件：

| 组件 | 文件 | 角色 |
|---|---|---|
| 编排器 | `scripts/bench-orchestrator.sh` | 常驻服务，接 PR webhook → 派发一次 gated 候选流 |
| 候选门禁流 | `scripts/bench-candidate.sh` | 候选分支隔离 → 构建候选镜像 → 并行 bench → 门禁 → merge + release |
| 镜像构建 | `scripts/build_runner_image.sh` | vendor 候选插件 + 构建/发布 runner 镜像 |
| 并行矩阵 | `scripts/run_k8s_matrix.sh` | 每 testcase 一个 k8s Job，收集日志 → 聚合 report |
| 单 case 入口 | `docker/entrypoint.sh` | 容器内跑一个 testcase，report 打进 pod 日志 |
| Job 模板 | `docker/job-template.yaml` | 每个 Job 的 k8s 清单（envsubst 渲染） |
| 结果聚合 | `scripts/aggregate_report.py` | per-case JSON → `report.json` + `report.md` |
| 发布 | `agentic-game-development/.github/workflows/release-on-bump.yml` | 版本变更 → 打包 zip + 建 GitHub Release |
| （旧）比较/发布 | `scripts/compare-and-maybe-release.sh` | v1 push 事后路径，**保留但当前不由编排器调用**（见文末） |

---

## 0. 前置状态（常驻）

- **编排器**以 `--mode http` 常驻监听 `0.0.0.0:8899`，`POST /trigger`，用 `x-bench-token` 鉴权。
  **仅 `pull_request` / `action=opened` 触发**。
  （备选 `--mode watch-logs`：直接 tail webhook receiver pod 日志，无需 receiver 改造；
  同样只对 `pull_request opened` 行触发。）
- **runner 镜像基仓**：`harbor.omgwow.ai/beaver_hub-public/aigdbench-runner`
  （候选流为每个 PR head 追加不可变 tag `:<sha8>`；镜像含 Godot + `claude` CLI + 候选插件 +
  `/app/testcases_filtered`）。
  > ⚠️ 不要用 `xiaojun_private/...`——本机 robot 无拉取权限，会 `ImagePullBackOff` 全失败。
- **k8s Secret `aigdbench-harness`**：`HARNESS_CMD`、`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、
  `ANTHROPIC_MODEL`、`IS_SANDBOX`。
- **k8s Secret `harbor-cred`**：docker-registry 拉取凭证（`beaver_hub-public` robot），已挂到 `default` serviceaccount。
- **基线文件**：`agentic-game-development/workflow/benchmark-baseline.json`，记 `best_score`
  （main 历史最高分）+ `best_version` + per-case 分数，作为门禁基准。

---

## 1. 收到 PR webhook

GitHub PR opened → webhook receiver → `POST http://<host>:8899/trigger`：

```
Header: x-bench-token: <BENCH_TRIGGER_TOKEN>
Body:   {"event":"pull_request","action":"opened","delivery":"<uuid>",
         "repo":"owner/repo","pr_number":42,
         "head_sha":"<sha>","base_ref":"main"}
```

（完整契约见 `docs/webhook-forward-contract.md`；编排器也接受嵌套的原始 GitHub payload
`"pull_request":{"number":..,"head":{"sha":..},"base":{"ref":..}}`。）编排器 HTTP handler
校验 token + JSON，若 `action != opened` 或非 PR 事件 → `202 {accepted:false, skipped}` 忽略；
否则提取 `delivery/repo/pr_number/head_sha/base_ref`，回 `202 {accepted:true}`
（**快速应答，benchmark 异步跑**），把这条 trigger 交给 bash 侧 `launch_candidate`。

---

## 2. 去重 + 建候选目录

`launch_candidate`（`bench-orchestrator.sh`）→ `bench-candidate.sh`：

1. **必须有 `head_sha`**（去重键 + 候选 commit）；缺失则 `PR event without head_sha; skipping`。
2. **去重**：键为 **`pr-<num>-<head_sha>`**，记在 `.orchestrator/seen`（`STATE_DIR`）；
   已见过则跳过——`synchronize` 对同一 head 重投递不会重复跑（尽管当前只在 opened 触发，
   去重键仍以 head.sha 为准，天然幂等）。
3. 建候选输出目录 **`results/cand-<delivery|sha8>/`**（源码 `ID="cand-${DELIVERY:-$SHA8}"`，
   `RESULTS_ROOT` 默认 `./results`）。

---

## 3. 候选流阶段 [1]–[8]（`bench-candidate.sh`）

护栏：写操作（merge / 版本 bump / push）**仅在 `--auto-release` 时发生**；否则跑到门禁为止、
只报告"会怎么做"。`--dry-run` 则做到 merge/bump 之前、不 push。

### [1] 候选分支
`git fetch origin main` → `git checkout -B bench-<sha8> origin/main` → `git cherry-pick <head_sha>`。
冲突 → 中止、记录、**不 merge**（`[1] CHERRY-PICK CONFLICT`）。

### [2] 临时版本
`<base-version>-bench.<sha8>`（prerelease semver）写进两个 manifest
（`.codex-plugin/plugin.json`、`.claude-plugin/plugin.json`），并写入 `_bench_source` 标记。

### [3] 候选镜像
`build_runner_image.sh` vendor `bench-<sha8>` 分支的插件 → `docker build`（Godot 4.5 +
`npm i -g @anthropic-ai/claude-code`）→ push **不可变 tag `:<sha8>`**（无 `:latest` 陈旧缓存风险）。
日志：**`results/cand-*/build.log`**。构建失败 → `ERROR: candidate image build failed`，终止。

### [4] 并行跑 benchmark（每 testcase 一个 k8s Job）

调 `run_k8s_matrix.sh -i <IMAGE:sha8> -d command -s aigdbench-harness -j 16 -n default
-D /app/testcases_filtered -t "<ids>" -o results/cand-* --no-push --no-build`。
矩阵每 testcase：

1. `envsubst` 渲染 `docker/job-template.yaml` → `kubectl create`；
   Job：`backoffLimit:0`、`activeDeadlineSeconds = TIMEOUT+300`（候选链路 `-T 1800`）、
   `ttlSecondsAfterFinished`（默认 **14400s/4h**）、`imagePullPolicy: Always`、`restartPolicy: Never`、
   `emptyDir` 挂 `/out`、`envFrom` 挂 harness Secret。
2. 容器 `entrypoint.sh` 内 `aigdbench run --driver command`，HARNESS_CMD =
   `claude -p {task} --dangerously-skip-permissions --plugin-dir /opt/agd-plugin`；
   folder/snapshot 型 → godot import → L0/L1 门禁 → 验证器打分；
   **report 打进 pod 日志**，包在 `<<<AIGDBENCH_REPORT_BEGIN/END>>>` 标记间。
3. **并发**：`-j 16` 门控在飞 Job 数。
4. **边完边收**：矩阵在每个 Job 完成的**当下立即** `kubectl logs` 抓标记间 JSON →
   `results/cand-*/<tc>.json`，pod 全量日志存 `logs/<tc>.pod.log`。
   > 必须"完成即收"，否则 `ttlSecondsAfterFinished` 会在收集前 GC 掉早完成的 Job → 空日志合成 0 分假记录。
   抓不到可解析 report → 合成 `status:error` 的 0 分记录（不留静默空洞）。

`aggregate_report.py` 把 per-case JSON 聚合成 **`report.json`**（`mean_score`、`count`、
`passed`、`testcases[]`）+ **`report.md`**，随后 `bench-candidate.sh` 回填
`report["trigger"]` = `{delivery, repo, commit, pr, candidate_branch, author, subject}`
作溯源。候选插件的实际 diff 另存为 **`plugin.diff`**（限插件子目录，截 4000 行）。

### [5] 门禁

读 `benchmark-baseline.json` 的 `best_score`（main 历史最高分，缺省 `0.0`/`(none)`）：

```
[5] gate: candidate mean=<NEW> vs historical best=<BEST> (from <best_version>) -> better=<0|1>
```

判据 **`mean_score > best_score`（严格优于历史最高）**。
- **不达标**（`<=`）：`Discarding candidate; no merge/release`——保留 report，结束，退出 0。
- **达标**：`Candidate qualifies for merge + release`；无 `--auto-release` 则打印"会怎么做"后结束。

### [6] 合并到 main（达标 + `--auto-release`）
并发防护：重读 `best_score`，若 main 在本候选运行期间已被别的 PR 抬高到 `>= NEW` →
`main advanced ... request rebase+retest`，不 merge。否则
`gh pr merge <pr_number> --repo <repo> --squash --admin`。

### [7] 基于 main 重打包
去掉 `-bench` 后缀 → semver bump（`--bump patch|minor|major`，默认 patch）两个 manifest →
`write_release_baseline` 刷新 `best_score`/`best_version`/per-case → commit。

### [8] 发布
push main → 触发插件仓 `release-on-bump.yml`（`[8] pushed vX.Y.Z to main`）。

### 候选目录产物一览 `results/cand-<delivery|sha8>/`
```
trigger.meta          # 触发面包屑（delivery/repo/commit/pr/candidate_branch/image）
build.log             # 候选镜像构建日志（[3]）
batch.log             # run_k8s_matrix 运行日志（[4]）
<tc>.json             # 每 testcase 结果
report.json           # 聚合报告（+ trigger 溯源 + mean_score）
report.md             # 可读汇总
plugin.diff           # 候选插件 vs origin/main 的 diff（限插件子目录，截 4000 行）
candidate.json        # 门禁判定 + 溯源（mean_score/historical_best/image/commit/pr/status）
logs/<tc>.job.yaml    # 渲染出的 Job 清单
logs/<tc>.pod.log     # 每 testcase 的 pod 全量日志（含 harness 输出）
```

---

## 4. 打包 + 建 Release（发布日志内容）

插件仓 `.github/workflows/release-on-bump.yml` 检测到 `plugin.json` version 变更后：

- **打包两个 zip**（内容一致，各留对应平台 manifest）：
  - `agentic-game-development-superpowers-claude-<version>.zip`
  - `agentic-game-development-superpowers-codex-<version>.zip`
- **建 GitHub Release**，tag `v<version>`（已存在且未 `force` 则跳过）。
- **Release 资产**：两个 zip + `benchmark-baseline-<version>.json`（该版本被 cut 时的基线快照，留痕）。

**release notes 溯源信息**：来源（PR#/head_sha/作者/subject）+ 测试数据
（mean、逐 case、delta vs best、镜像 digest），与 `spec-v2.md` 一致。分数/provenance 也通过随发布
上传的 `benchmark-baseline-<version>.json`（含 `best_score`/`count`/`image`/`plugin_commit`/per-case）体现；
"这次跑到底测了什么插件内容 + 得了多少分"完整记录在 `results/cand-*/`：`report.json`
（`trigger` + `mean_score` + 逐 case）、`candidate.json`（门禁判定 + 溯源）、`plugin.diff`（插件改动）。

---

## 端到端时序（`--auto-release`）

```
GitHub PR opened
  → webhook receiver → POST :8899/trigger {event:pull_request,action:opened,delivery,repo,pr_number,head_sha,base_ref}
    → 编排器: action=opened? → 去重(pr-<num>-<sha>) → launch_candidate → bench-candidate.sh
      → [1] 候选分支 bench-<sha8> = origin/main + cherry-pick head_sha
      → [2] 临时版本 <base>-bench.<sha8> 写进两个 manifest
      → [3] build_runner_image.sh: vendor 候选插件 → docker build → push :<sha8>          (build.log)
      → [4] run_k8s_matrix.sh: 每 testcase 一 Job (claude+候选插件) → 边完边收 → aggregate  (report.json/.md)
      → [5] 门禁: mean_score > best_score ?  ── 否 → 丢弃候选, 保留 report, 结束
      → [6] gh pr merge <pr> --squash --admin  (并发防护: 重读 best)
      → [7] 去 -bench 后缀 + semver bump + 刷新 best_score/best_version
      → [8] push main
        → 插件仓 release-on-bump.yml: 打 2 个 zip + 建 Release v<version> + 上传基线快照
```

## 关键默认值 / 覆盖

| 项 | 默认 | 覆盖 |
|---|---|---|
| 触发事件 | `pull_request` / `action=opened` | （固定） |
| 镜像基仓 | `beaver_hub-public/aigdbench-runner` | `--image-repo` / `IMAGE_REPO` |
| 候选镜像 tag | 不可变 `:<head_sha8>` | （固定） |
| 并发 | 16 | `--jobs` |
| testcase 集 | `/app/testcases_filtered`（30 个） | `--testcases-dir` |
| harness Secret | `aigdbench-harness` | `--secret` |
| Job 超时 | deadline = TIMEOUT+300（候选 `-T 1800`） | `--timeout` |
| Job TTL | 14400s (4h) | `TTL_AFTER_FINISHED` |
| 门禁 | **严格 `mean_score > best_score`** | （固定） |
| 版本 bump | patch | `--bump` |
| 自动合并+发布 | 关（仅报告，跑到门禁为止） | `--auto-release` |

---

## 附：v1 push 事后路径（保留但当前不派发）

历史上编排器还有一条 **push 触发的事后验证路径**：`launch_batch`（`bench-orchestrator.sh`）
→ `compare-and-maybe-release.sh`，门禁是 `delta >= min-delta`（"不低于当前 release 即发"），
commit 已在 main、事后测。收敛为"仅 PR opened 触发"后，编排器**不再派发** `launch_batch`，
该函数与 `compare-and-maybe-release.sh` 均**保留在代码中但已到达不能**。如需对某个已在 main 的
commit 做事后对比/发布，可手动调用 `compare-and-maybe-release.sh`（见其 `-h`）。
本规格的正文（§0–§4）只描述当前生效的 PR 触发 gated 流程。
