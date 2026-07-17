# AIGameDevBench 开发者文档：架构 · 使用 · 原理

> 本文面向开发者，系统梳理 AIGameDevBench 的整体架构、使用方式、底层原理，并列出关键路由与代码路径。
> 用户向的快速上手见 [`README.md`](../README.md)；规范见 [`spec-v2.md`](spec-v2.md)。

---

## 1. 定位与核心契约

AIGameDevBench 是一个 **SWE-bench 风格的受控基准**，评测 AI 编码 harness 做 **Godot 游戏开发**的能力。

一个 **testcase** = （游戏 repo 在某基线状态）+（一个任务）+（一个冻结的黄金验证器）。

**核心契约**（贯穿全系统的不变量）：

> **什么都不做（noop）必须得 0 分；正确改动（golden）必须得满分 1.0。**

这条契约由 `aigdbench audit` 强制校验（见 §7）。任何违反它的 testcase 被视为不健康（`leaky_baseline`、`patch_not_one` 等）。

---

## 2. 一次评测的生命周期

无论从 CLI、dashboard 还是 k8s 触发，单个 testcase 的执行流程都收敛到 `runner.run_testcase()`：

```
准备工作区 (workspace)
   │  git-type: checkout baseline_ref;  folder-type: copytree baseline/ 或 _snapshots/<snap>
   ▼
driver.run(task, workspace)          ← harness 在此改文件 (NoOp/Patch/CommandHarness)
   │
   ▼
捕获 driver 产出的 diff + outcome
   ▼
run_validation(workspace, changed_files)   ← L0 语法/场景加载 + L1 资源/signal 完整性
   ▼
verifier.verify(testcase, workspace)        ← 黄金验证器按 scoring_mode 打分
   ▼
VerifierResult{score, status, checks[]}
```

关键源码：
- `src/aigamedevbench/runner.py` — `run_testcase()`（编排一次执行）
- `src/aigamedevbench/workspace.py` — `isolated_workspace()`（git-type）/ `folder_workspace()`（folder-type）
- `src/aigamedevbench/driver.py` — 三种 driver
- `src/aigamedevbench/validation.py` — L0/L1
- `src/aigamedevbench/verifiers/` — 验证器注册表

---

## 3. Testcase 数据模型

`src/aigamedevbench/testcase.py` 定义（`testcase.toml` 解析入口 `load_testcase`）：

### 3.1 category（5 类能力维度）
```
behavior_logic       行为/逻辑正确性
intent_translation   把自然语言意图翻译成正确实现
precise_edit         精确定点修改
architecture         架构/数据边界/解耦
visual_audio         视觉/音频/布局
```

### 3.2 source_kind（工作区来源）
- **`git`** — checkout `baseline_ref`（真实 repo 的某 commit）。
- **`folder`** — 工作区来自本地 `baseline/` 目录，或（当设了 `snapshot` 字段时）来自共享快照 `<testcases-dir>/_snapshots/<snapshot>/`。快照被 gitignore，由 `scripts/make_snapshots.py` 按需生成（见 §8）。

### 3.3 scoring_mode（`result.py::VerifierResult.from_checks`）
| mode | 计分 | 用途 |
|------|------|------|
| `checkpoints` | passed / total | 默认；每个 checkpoint 等权 |
| `gated` | noop=0 / golden=1（忽略纯文档性 check） | survey 迁移用例 |
| `tristate` | 0 / 部分 / 1 | 三态 |
| `weighted` | 按权重 | 加权 checkpoint |
| `fields` | 字段匹配 | 配置类 |

> **踩坑要点**：`checkpoints` 模式下，若 setup 类 check 在 baseline 也通过，会给 noop 漏分（`leaky_baseline`）。约定做法（见 `ability-cooldown-gate`）：把 setup 变成**硬前置条件**，让第一个计分 checkpoint 就是判别点，从而 noop 严格 = 0。

---

## 4. Driver（harness 抽象）

`src/aigamedevbench/driver.py`，三种实现（`_make_driver` 在 cli.py 里选择）：

- **`NoOpDriver`** — 什么都不做。用于验证"noop=0"（audit 的下界）。
- **`PatchDriver`** — 直接 `git apply` 一个 diff（通常是 `fix.diff`）。用于验证"golden=1"（audit 的上界）。
- **`CommandHarnessDriver`** — 真正评测对象：在工作区内跑一条 CLI harness 命令。
  - 模板占位符：`{task}`（原始任务文本作为单参数）、`{task_file}`（workspace/TASK.md）、`{workspace}`。
  - **无 shell**（shlex 切分），任务文本任意字符不会注入。
  - stdin 关闭 → 交互式提示得到 EOF 快速失败而非挂起。
  - 防挂起三重保护：整体 `timeout`；`APPROVAL_MARKERS` 命中即中止（harness 卡在权限审批）；可选 `stall_timeout`（默认关闭，因为很多 harness 如 `claude -p` 在完成前不产出）。
  - 输出按行流式落日志 + 解析为结构化事件（`harness_events.py`，`harness_format` = auto/stream-json/text）。

---

## 5. 验证器（Verifier）

注册表 `src/aigamedevbench/verifiers/__init__.py`（`@register("name")`）。8 种类型：

| verifier_type | 文件 | 原理 |
|---------------|------|------|
| `godot_scene_assert` | godot_runtime.py | 注入 `verifier_scene.tscn`（把 `main.tscn` 挂为子节点 "Main"）+ `verifier.gd`，headless 启动，脚本 print `{"assertions":[...]}`，按 checkpoint 计分。**需要完整的 class 缓存**（否则报 `import cache incomplete`）。 |
| `godot_scenetree` | godot_runtime.py | `--script` 模式跑黄金 .gd。 |
| `survey_bad_case` | survey_bad_case.py | 回归型：要求 harness 改了相关源文件、diff 不等于原 `bad.diff`、oracle 可区分（L0/L1 回归 或 非空 bad.diff）、通过 L0/L1 gate。 |
| `py_config` | config_verifier.py | 校验配置文件字段。 |
| `py_tscn_diff` | tscn_diff_verifier.py | 比对 .tscn 结构差异。 |
| `py_gdscript_ast` | arch_verifier.py | GDScript AST/架构检查。 |
| `visual_static` | godot_runtime.py | 结构层视觉断言（截图/SSIM 层另计）。 |
| `interaction_routing` | godot_runtime.py | 注入鼠标事件、走焦点链，验证点击路由/tab 顺序。 |

`godot_scene_assert` 注入机制（`godot_runtime.py::_run_godot_scene`）：把 `verifier_scene.tscn` 里的 `__VERIFIER_GD__` 占位符替换成注入的临时脚本名，headless `--quit-after N` 跑，解析 stdout 里的 assertion JSON，跑完删除注入文件。

---

## 6. 验证门（L0 / L1）

`src/aigamedevbench/validation.py`，在 verifier 之前跑，作为基础健康门：

- **L0**（`run_l0`）：GDScript 语法检查 + headless 启动每个场景 2 帧，stderr 含 `ERROR` 即判 `L0 crash`。**排除 `addons/` 前缀**（第三方插件的编辑器代码不算）。
- **L1**（`run_l1`）：场景资源引用完整性（`check_script_references`）+ signal target 完整性（`check_signal_targets`）。

> 注意：`godot_scene_assert` 的 `check_class_cache` 会扫描**所有** `.gd`（含 addons/），与 L0 的排除策略不一致——带 gut/gdUnit4 等测试框架 addon 的项目要在快照生成时剥离（见 §8）。

---

## 7. CLI 命令（`aigdbench`）

入口 `src/aigamedevbench/cli.py`（`main` group）。8 个命令：

| 命令 | 作用 |
|------|------|
| `list` | 列出 testcases。 |
| `audit` | **健康校验**：noop 必须 0、golden(`fix.diff`) 必须 1。输出 flags：`leaky_baseline` / `patch_not_one` / `no_fix_diff` / `overbroad_oracle`。 |
| `smoke` | 单 testcase 冒烟（noop/patch）。 |
| `probe-permissions` | 探测 harness 的权限提示行为。 |
| `scaffold` | 生成新 testcase 骨架。 |
| `run` | **核心**：跑一个/多个 testcase，支持 `--driver noop\|patch\|command`、`--harness-cmd`、`--repeat`、`--jobs`。 |
| `compare` | 对比多次 run。 |
| `serve` | 启动 dashboard（见 §9）。 |

审计需要 godot；本地无 godot 时通过 runner 镜像跑：
```bash
docker run --rm -v $PWD/testcases_filtered:/work/tf \
  --entrypoint aigdbench <runner-image> \
  audit --testcases-dir /work/tf --only <id> --godot-binary godot
```

---

## 8. 镜像与快照

### 8.1 Runner 镜像（`docker/`）
- `Dockerfile` — Godot 4.5 + claude CLI + Python + testcases + vendored 插件。
- `entrypoint.sh` — 单 testcase 执行入口（k8s Job 用），report 包在 `AIGDBENCH_REPORT` 标记里打到 pod 日志。
- `job-template.yaml` — k8s Job 模板。
- `vendor/agd-plugin` — 构建时 vendored 的插件快照。
- 构建：`scripts/build_runner_image.sh`。

### 8.2 项目快照（`_snapshots/`）
`scripts/make_snapshots.py`：读每个 testcase 的 `snapshot.json`（`repo_url` + `base_ref`），clone 一次进本地 cache，`git archive` base commit 的树到 `<testcases-dir>/_snapshots/<snapshot>/`（无 .git、无 import 缓存，运行时再生成）。
- 快照被 **gitignore**，仓库只跟踪 `snapshot.json` + `fix.diff`。
- 会**剥离编辑器测试框架 addon**（`addons/gut`、`addons/gdUnit4`）：它们声明大量 `class_name` 但 headless 编译不全，会让全局类缓存不完整、触发 `godot_scene_assert` 的 import-cache 守卫。游戏本身不引用它们。

---

## 9. Dashboard（`aigdbench serve`）

纯 stdlib HTTP server（默认 :8000）。两块职责分离：
- **HTTP server + 路由分发**在 `src/aigamedevbench/cli.py::serve_cmd` 里的 `Handler`（`do_GET`/`do_POST` 用 `path == "/api/..."` 精确匹配）。
- **前端 SPA（HTML/JS）+ 数据整形**在 `src/aigamedevbench/webreport.py`（hash 路由的单页应用，`/api/summary`、`/api/detail` 的渲染逻辑）。

### 9.1 关键路由（服务端，`cli.py::serve_cmd`）

| 方法 | 路由 | 作用 |
|------|------|------|
| GET | `/api/summary` | 所有 run 的汇总。 |
| GET | `/api/detail` | 单个 run/testcase 明细。 |
| GET | `/api/config` | dashboard 配置。 |
| GET | `/api/testcases` | testcase 列表。 |
| GET | `/api/testcase` | 单 testcase 详情（含文件）。 |
| POST | `/api/testcase/create` | 新建 testcase。 |
| POST | `/api/testcase/save-file` | 保存 testcase 文件。 |
| POST | `/api/testcase/delete-file` | 删除 testcase 文件。 |
| GET | `/api/images` | 可选 runner 镜像 tag（beaver_hub-public）。 |
| POST | `/api/runs/start` | 启动一次 matrix run（调 `run_k8s_matrix.sh`）。 |
| POST | `/api/runs/stop` | 停止 run。 |
| GET | `/api/runs/status` | run 实时状态/进度。 |
| POST | `/api/runs/external` | 外部（webhook receiver）上报 candidate run 状态，供 Live/Status 页展示。 |
| GET | `/api/webhooks` | 读 `webhooks.jsonl`，Webhooks 页展示。 |

### 9.2 启动
`scripts/start_dashboard.sh`：在 detached tmux 会话 `aigdbench-web` 里同时起两个窗口——`dashboard`（serve :8000）和 `webhook-receiver`（:8899）。`--stop` 停会话，`--foreground` 只前台起 dashboard。

---

## 10. Webhook 自动触发链路（PR → gated candidate → 自动发布）

完整链路：

```
GitHub PR (opened)
   │  GitHub webhook
   ▼
k8s github-webhook receiver
   │  转发 BENCH_TRIGGER_URL = http://<host>:8899/trigger
   ▼
scripts/webhook_receiver.py  (:8899, tmux 里由 start_dashboard.sh 拉起)
   │  ① 记账 .orchestrator/webhooks.jsonl (dashboard Webhooks 页读)
   │  ② autorun-mode=candidate → 触发
   ▼
scripts/bench-candidate.sh
   │  1. 隔离候选分支: git checkout -B bench-<sha> origin/main; cherry-pick <sha>
   │  2. 临时版本号: <main-ver>-bench.<sha8>
   │  3. 构建候选镜像 image:<sha> (immutable tag, 无 :latest 陈旧缓存风险)
   │  4. 并行 benchmark: 每 testcase 一个 k8s Job (claude + 候选插件)
   │  5. gate: mean_score > main 历史最佳? 否→丢弃候选、留报告、exit 0
   │  6. 合并 main + repackage(正式版本号) + 推送
   ▼
release-on-bump.yml 发布新 release
```

### 10.1 webhook_receiver 关键逻辑（`scripts/webhook_receiver.py`）
- 路由：`POST /trigger`（接收 delivery）、`GET /healthz`。
- 鉴权：可选 `x-bench-token`（`--token`，空=不鉴权）。
- 事件分类（对齐 `bench-orchestrator.sh`）：非 PR → skipped；`action != opened` → skipped；head 分支以 `release-v` 开头（自己开的版本号 PR）→ **skipped**（防死循环）；否则 accepted。
- 并发保护：`cand_lock` + `running`（同时只跑一个 candidate）；`seen` 集合按 `pr_number-head_sha` 去重（重投递不重跑）。
- 三种 autorun-mode：
  - **`candidate`（推荐）** — 跑 `bench-candidate.sh`，评测"合并后插件"，带 `--auto-release` 才严格超过历史最佳时合并发布。
  - **`matrix`** — POST `/api/runs/start`，用共享 `:latest` 镜像跑全套（**不反映 PR 的插件改动**）。
  - **`off`** — 只记账。

### 10.2 legacy 事后流程
`bench-orchestrator.sh` + `compare-and-maybe-release.sh`：commit 已在 main，benchmark 只校验"不低于基线"就发布。candidate 流程（隔离 + 严格更优才合并）是更安全的替代。

---

## 11. 并行执行：k8s 矩阵 vs 本地矩阵

| 脚本 | 原理 |
|------|------|
| `scripts/run_k8s_matrix.sh` | 从源码 build+push 镜像 → 每 testcase 一个 k8s Job（gate 在途数量）→ 从每个 Job 的 pod 日志刮 `AIGDBENCH_REPORT` 进 `results/<id>.json` → 聚合成 `report.json`/`report.md`。无需共享 PVC。 |
| `scripts/run_local_matrix.sh` | 本地 `docker run` 每 testcase 的等价物。 |
| `scripts/run_bench50.sh` | 固定 50 用例基准跑。 |

Job 参数：`-i 镜像`、`-j 并发`、`-d driver`、`-t "id..."`、`-T 每例超时`、`-A activeDeadlineSeconds`（硬杀 = timeout+300）、`-s HARNESS_SECRET`。

> **运维踩坑**：Job 用不存在于 registry 的 tag（如手动传 `:latest` 但 registry 无此 tag）→ 全 pod `ImagePullBackOff`。orchestrator 会持续按 gate 重建 Job，**光删 pod/Job 无用**，必须先杀 orchestrator 进程再删 Job。candidate 流程用 immutable commit tag 正是为规避 `:latest` 陈旧/缺失问题。

---

## 12. 报告结构

聚合 `report.json` 关键字段（`scripts/aggregate_report.py` / matrix 脚本产出）：
```
driver, image, harness_cmd, namespace, count, passed, mean_score,
testcases[ {testcase_id, category, score, status, l0_l1_pass, wall_time, exit_code, error, log} ],
trigger{ delivery, repo, commit, pr, ... }   ← webhook 触发时
```
candidate run 额外产出 `candidate.json`（`status, commit, image, mean_score, historical_best, better`）。

---

## 13. 关键路径速查

| 关注点 | 路径 |
|--------|------|
| 单次执行编排 | `src/aigamedevbench/runner.py::run_testcase` |
| Driver（harness 抽象） | `src/aigamedevbench/driver.py` |
| L0/L1 门 | `src/aigamedevbench/validation.py` |
| 验证器注册 + 实现 | `src/aigamedevbench/verifiers/` |
| Testcase 模型/解析 | `src/aigamedevbench/testcase.py` |
| 打分（scoring_mode） | `src/aigamedevbench/result.py::VerifierResult.from_checks` |
| 健康审计 | `src/aigamedevbench/testcase_audit.py` |
| CLI 命令 | `src/aigamedevbench/cli.py` |
| Dashboard + 路由 | `src/aigamedevbench/webreport.py` |
| Webhook 接收 | `scripts/webhook_receiver.py` |
| Candidate gated 发布 | `scripts/bench-candidate.sh` |
| k8s 并行矩阵 | `scripts/run_k8s_matrix.sh` |
| 快照生成 | `scripts/make_snapshots.py` |
| 镜像构建 | `scripts/build_runner_image.sh` + `docker/Dockerfile` |
| Dashboard/receiver 启动 | `scripts/start_dashboard.sh` |

---

## 14. 常见开发流程

```bash
# 本地跑一个 testcase（noop 应 0）
aigdbench run --testcases-dir testcases_filtered --testcase <id> --driver noop

# golden 应 1
aigdbench run --testcases-dir testcases_filtered --testcase <id> --driver patch

# 审计健康（noop=0 / golden=1）
aigdbench audit --testcases-dir testcases_filtered --only <id> --godot-binary godot

# 评测真实 harness
aigdbench run --testcases-dir testcases_filtered --testcase <id> \
  --driver command --harness-cmd "claude -p {task} --dangerously-skip-permissions"

# 生成快照后本地矩阵
python3 scripts/make_snapshots.py --testcases-dir testcases_filtered
scripts/run_local_matrix.sh ...

# 启动 dashboard + webhook receiver
scripts/start_dashboard.sh
```
