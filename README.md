# AIGameDevBench

一个 SWE-bench 风格的**受控基准**，用来评测 AI 编码 harness 做 **Godot 游戏开发**的能力。

每个 testcase = （某个游戏 repo 在某基线 commit 的状态）+（一个任务）+（一个冻结的黄金验证器）。
harness 的改动由自动验证器打分。核心契约：**什么都不做必须 0 分，正确改动得满分。**

---

## 目录

- [安装](#安装)
- [核心概念：一次评测怎么跑](#核心概念一次评测怎么跑)
- [快速开始](#快速开始)
- [评测一个真实的 AI harness](#评测一个真实的-ai-harness)
- [Dashboard 可视化](#dashboard-可视化aigdbench-serve)
- [正交实验](#正交实验experiment-yaml)
- [Testcase 数据集](#testcase-数据集)
- [验证器类型](#验证器类型)
- [命令速查](#命令速查)
- [脚本一览](#脚本一览scripts)
- [Webhook 自动触发](#webhook-自动触发pr-opened--gated-候选--自动合并发布)
- [运行测试](#运行测试)
- [开发者文档](#开发者文档) — 架构 · 原理 · 关键路由（[`docs/dev.md`](docs/dev.md)）
- [BeaverHub 运行](#beaverhub-运行) — 把 testcase 作为平台任务跑（[`.beaver/tasks/aigdbench/README.md`](.beaver/tasks/aigdbench/README.md)）

---

## 安装

```bash
git clone https://github.com/wz306097/AIGameDevBench.git
cd AIGameDevBench
python -m pip install -e ".[dev]"
```

- Python ≥ 3.10。
- **纯 Python 验证器**（`py_config` / `py_tscn_diff` / `py_gdscript_ast`）不需要 Godot。
- **运行时验证器**（`godot_scene_assert` / `godot_scenetree` / `visual_static` / `interaction_routing`）
  需要一个 `godot` 可执行文件在 PATH 上，或用 `--godot-binary` 指定路径。

入口命令是 `aigdbench`。若未进 PATH，可用
`python -c "from aigamedevbench.cli import main; main()"` 接同样的子命令代替。

---

## 核心概念：一次评测怎么跑

```
runner:
  准备隔离工作区                        # 见下方"两种起点"
  driver.run(task, workspace)          # noop | patch | command(你的 AI harness)
  godot --import (folder 型)            # 构建资源缓存，让场景能加载
                                        #   ↳ 缓存已在且未改动可导入资产时自动跳过(省~2.5s/次)
  L0/L1 门禁                            # 场景能加载？无坏引用？否则直接 0 分
  注入黄金验证器 -> 打分                 # 打完分即删除（防作弊）
```

**两种起点形态**（每个 testcase 由 `source_kind` 决定）：

| `source_kind` | 起点 | 在哪里跑 |
|---|---|---|
| `folder`（自包含） | testcase 自带的 `baseline/` 目录，拷进临时区并 `git init` | **任意目录** |
| `git` | 源游戏 repo 的某个 commit，用 `git worktree` checkout | **必须在那个游戏 repo 内** |

folder 型自包含、随处可跑；git 型必须在目标游戏 repo 内跑。

**三种 driver：**

| `--driver` | 作用 | 必带参数 |
|---|---|---|
| `noop` | 什么都不改，基线对照（应得 0） | — |
| `patch` | 应用一个现成 diff（回放正确改动，或离线评一个 AI 的产出） | `--patch <file>` |
| `command` | 在工作区内现场调用任意命令行 harness 完成任务 | `--harness-cmd '<模板>'` |

---

## 快速开始

`gdb-task_0002` 是 folder 型，随处可跑（需要 Godot 来启动场景打分）。

```bash
TCDIR=./testcases

# 列出全部 testcase
aigdbench list --testcases-dir "$TCDIR"

# 一键准入检查：noop 必须 0 分，fix.diff 必须 1 分
aigdbench smoke --testcases-dir "$TCDIR" --testcase gdb-task_0002 --godot-binary /path/to/godot

# 调试验证器时可分别跑两个原始动作
aigdbench run --testcases-dir "$TCDIR" --testcase gdb-task_0002 --driver noop
aigdbench run --testcases-dir "$TCDIR" --testcase gdb-task_0002 \
  --driver patch --patch "$TCDIR/gdb-task_0002/fix.diff"
```

期望结果：

| driver | status | score |
|---|---|---|
| noop | fail | 0.00 |
| patch (fix.diff) | pass | 1.00 |

---

## 评测一个真实的 AI harness

用 `--driver command` 自动跑任意 CLI harness。driver 会把 testcase 的 `task` 写进
`workspace/TASK.md`，把占位符替换进你的命令模板，在隔离工作区里执行，然后给它改出来的东西打分。

**占位符：** `{task}`（任务原文，作为单个参数）、`{task_file}`（`TASK.md` 路径）、`{workspace}`（工作区目录）。

```bash
aigdbench run --testcases-dir ./testcases_filtered \
  --driver command \
  --harness-cmd 'claude -p {task} --dangerously-skip-permissions' \
  --harness my-claude-code \
  --timeout 900 \
  --godot-binary /path/to/godot \
  --log-dir ./harness-logs \
  --workspace-root ./bench-workspaces \
  --report ./report.json
```

**harness 必须全自动运行。** `--driver command` 不带 TTY、关闭 stdin，任何交互式提示都无法应答，
会一直阻塞到被中止。务必传入让 harness 进入无人值守/自动批准模式的参数：

| harness | 自动模式参数 |
|---|---|
| Claude Code | `claude -p {task} --dangerously-skip-permissions`（或 `--permission-mode bypassPermissions`） |
| Codex | `codex exec --dangerously-bypass-approvals-and-sandbox --cd {workspace} {task}` |

其它要点：

- **并行评测**：`--jobs N`（`-j N`）同时跑 N 个 testcase（默认 1 = 串行）。harness 与 Godot 都是子进程，
  线程池并行其 I/O 等待即可。每个 testcase 用独立工作区和独立 driver，**结果与串行完全一致**（含顺序、分数、
  failure_stage）;report 里 testcase 顺序仍按输入顺序排，稳定可 diff。跑 runtime（Godot）验证器时注意机器负载。
- **重复评测求方差**：`--repeat N`（默认 1）让每个 testcase 跑 N 次。AI harness 有随机性，单次运行分不清
  「真强」还是「运气好」。开了之后每个 testcase 的 report 记录带一个 `repeat` 块（每次 attempt 的分数列表、
  mean、std、95% 置信区间、pass@1、各 attempt 的 failure_stage 分布），记录的 `score` 取 N 次均值;report 顶层
  还写 `mean_score_ci95`（整套的置信区间）。屏幕摘要打成 `mean X.XXX ± Y.YYY [95% CI ...]`。与 `--jobs` 组合时，
  (testcase × attempt) 会被展平进同一个线程池，最大化并行。dashboard 的均分柱会画出置信区间带，
  点开某 case 能看到 N 次分数的分布小图。（CI 用正态近似，N 小时仅作离散度参考，非严格区间。）
- harness 输出**实时打印到屏幕**（带 testcase id 前缀），卡住的提示第一时间可见；`--no-stream` 关闭。
  `--jobs > 1` 或 `--repeat > 1` 时自动改为「整块汇总」：每个 testcase/attempt 完成时一次性打印它的完整输出块，不逐行交错;
  完整日志仍逐个写 `--log-dir`。
- **harness 内部活动记录**：`--harness-format {auto,stream-json,text}`（默认 `auto`）把 harness 的 stdout
  解析成结构化的**逐 turn 事件**（tool 调用、输出、token 用量），写进 report 的 `ai_agent_context`，
  dashboard 的 AI activity 面板会像展示 survey 行一样把它们逐 turn 展开。`auto` 先按行试 stream-json 再回退到
  文本启发式;若 harness 能吐 JSON 事件流（如 Claude Code `--output-format stream-json --verbose`），
  用 `stream-json` 最精确、还能拿到 token 数。
- **超时保护**：`--timeout` 是单 testcase harness 上限，默认 1200 秒（20 分钟）；另有审批提示检测器，
  识别到 "waiting on your permission approval" 之类会立即中止并给出提示。
- 任何失败（超时/审批阻塞/非零退出）都会把日志尾部打到屏幕，该 testcase 记 0 分，批次继续。
- **`--stall-timeout` 默认关闭**：它在 N 秒无输出时中止，但 `claude -p` 等非流式 harness 完成前不打印任何东西，
  "无输出"其实是"还在干活"，开了会误杀健康的长任务。请用 `--timeout` 作为真正的上限。
- **harness 不要 commit 自己的改动**：runner 用 `git status --porcelain` 检测变化，一旦 commit 工作区就干净了，
  检测不到改动记 0 分。改完留在工作区即可。
- **`--workspace-root`**：默认工作区建在系统临时目录下。若 harness 限制可编辑目录，
  把它指到 harness 信任的目录（如项目内某目录）。它只决定工作区位置，不替代上面的自动模式参数。

`--report` 写一份 JSON 汇总（每个 testcase 的 score / status / wall_time / exit_code /
stalled·blocked 标志 / 日志路径，以及总体均值）。完整输出也写到 `--log-dir`。

每条 testcase 记录还带两个用于**结果分析**的结构化字段：

- **`failure_stage`** —— 这次运行失败/停在哪一层，便于画失败漏斗、区分「流程失败」与「能力不足」：
  `no_change`（harness 什么都没改）/ `harness_error`（超时·卡死·审批阻塞·非零退出）/
  `l0` · `l1`（准入门禁拒绝了改动）/ `verifier`（过了门禁但验证器自身报错）/
  `none`（验证器正常跑完——score 可能仍 <1，那是能力差距而非流程失败）。
- **`timings`** —— 分层耗时（毫秒）：`import_ms` / `l0_ms` / `l1_ms` / `verifier_ms` / `total_ms`
  （command driver 另有 `harness_ms`）。用于**耗时 / 正确性 tradeoff** 分析——例如 folder 型
  case 的 `import_ms`（godot 资源导入）本是整条 pipeline 的耗时大头（~2.5s 的 Godot 启动+扫描）。
  另有布尔 `import_skipped`：当 workspace 已有 `.godot/` 缓存、且 harness 未改动任何可导入资产
  （`.png`/`.ogg`/`.svg`/`.import` 等）时，这个冗余的 import pass 会被**跳过**，`import_ms` 归零。
  script/scene（`.gd`/`.tscn`/`.tres`）改动不触发重导入;无缓存、改了资产、或改动未知时照常 import
  （对正确性零风险）。这在 `--repeat N` 下收益乘以 N。

批次结尾会打印一行 `--- failure stages: ...` 汇总；dashboard 的 timing 视图也会显示每层均耗时与失败漏斗。

> **示例脚本**：`scripts/run_bench50.sh` 是一个用 `claude -p` 跑整套 `testcases_filtered/`
> 的完整脚本，结果与日志都落在 `bench_runs/` 下。建议在 `tmux` 里运行以便断开重连。

**手动回路**（不想用 command driver 时）：手工完成任务 → `git diff > ai.diff` →
`aigdbench run --driver patch --patch ai.diff` 出分。

> **patch 应用的鲁棒性**：`--driver patch` 应用 diff 时容忍几类常见的「语义正确但格式不规整」的补丁——
> CRLF/LF 与空白差异（`--ignore-whitespace`）、hunk 头 `@@ -a,b +c,d @@` 行数算错（`--recount`，
> 以正文的 +/-/context 行为准）、以及 hunk 内空行丢了行首空格（自动补 `" "`）。这些在 harness/LLM
> 产出的 diff 里很常见;基准只看语义改动，所以不因格式瑕疵拒绝一个正确的补丁。

---

## 正交实验（Experiment YAML）

`aigdbench run --experiment` 用配置文件批量跑正交 cell。每个 cell 固定一组变量：

```text
model × agent_cli × orchestration × task_set × harness_components
```

`task_set` 指向 AIGameDevBench testcase 集合；集合列表集中维护在
`experiments/testsets.yaml`，experiment YAML 通过 `test_set_file: testsets.yaml` 引用它。
当前内置集合：

- `filtered_30`：`testcases_filtered/` 的精选 strong-oracle case（数量与分布以 `testcases_filtered_manifest.json` 为准，由 `scripts/make_manifest.py` 生成）。
- `smoke_2`：两个代表性 smoke case，用于快速验证 experiment wiring。
- `all`：完整 `testcases/` 目录，不显式列 testcase，运行时扫描目录。

`testsets.yaml` 支持为集合配置 `default_timeout`，并为单个 testcase 覆盖：

```yaml
test_sets:
  smoke_2:
    testcases_dir: ../testcases_filtered
    default_timeout: 1200
    testcases:
      - id: wave-combat-score-system
        timeout: 1200
      - id: gdb-task_0281
        timeout: 1200
```

experiment run 会把每条 testcase 的 `wall_time` 写进 report；如果 testcase 在
`testsets.yaml` 里显式列出，run 结束后会按结果自动调整该条 `timeout`：超时/卡死时翻倍，
正常完成时保留当前值或提升到 `ceil(wall_time × 1.5)`。

`harness` 指向 harness 配置集合；集合列表集中维护在 `experiments/harnesses.yaml`，
experiment YAML 通过 `harness_file: harnesses.yaml` 引用它。每个 harness 都声明
`target_agent_cli`，用于区分 `generic`、`codex`、`claude-code` 等运行面，避免把
Codex-only 配置误用到 Claude Code cell。当前内置配置：

- `gd-baseline`：空 harness surface，适合作为非 Codex / patch smoke baseline。
- `bare-codex`：只保留 Codex 运行所需最小配置，不带用户 `AGENTS.md`、skills、MCP、hooks、memory。
- `skill-only`：只带 `agentic-game-development` skill。
- `agents-md-skill-mcp`：带用户 `AGENTS.md` 和 game-dev skill。
- `all-codex`：Codex-only 全量配置，带 repo-local
  `agentic-game-development-superpowers` plugin、`fast-context` / `transcript-viewer`
  skills、`node_repl` MCP、hooks、CLI config 和 memory summary。

示例：

```yaml
id: filtered30-smoke
test_set_file: testsets.yaml
harness_file: harnesses.yaml

cells:
  - id: baseline_filtered_smoke
    task_set: smoke_2
    harness: gd-baseline
```

`harness` 会解析成七类 `harness_components`，并写入稳定的 `harness_fingerprint`，用于后续按 model / harness / orchestration 分组比较。
当 `agent_cli: codex` 时，runner 会为每个 cell materialize 独立的 `CODEX_HOME`，让 `bare-codex`
和 `all-codex` 不只是 metadata 不同，而是真正改变 Codex 能看到的 instruction / skill /
MCP / hook / memory surface。

```bash
# 只展开 cell，不运行 testcase
aigdbench run --experiment experiments/filtered30-smoke.yaml --dry-run

# 用每个 testcase 自带 fix.diff 做 smoke；纯 Python verifier 不需要 Godot
aigdbench run --experiment experiments/filtered30-smoke.yaml \
  --driver patch --results-dir results
```

输出按 cell 隔离：

```text
results/<experiment_id>/<cell_id>/
  manifest.json
  report.json
  final_results.json
  final_results.csv
  artifacts/
  logs/
```

`manifest.json` 记录复现实验所需的配置真源：`model`、`agent_cli`、`orchestration`、
`task_set`、`testcases_dir`、`testcase_ids`、`harness_preset`、`harness_components` 和
`harness_fingerprint`，同时写入 `git_commit`、`runner_version`、`started_at`。Codex cell
额外写 `codex_harness_runtime`，包含隔离 `CODEX_HOME` 路径和 `codex debug prompt-input`
probe 摘要；probe 只保存 `stdout_sha256`、字节数和 visibility flags，不保存完整 prompt。

`report.json` 保留旧 dashboard 兼容路径；`final_results.json` 是同内容的 cell 结果真源，
`final_results.csv` 把每条 testcase result 展平成可直接 groupby 的表格，并复制
`experiment_id`、`cell_id`、`model`、`agent_cli`、`orchestration_*`、`task_set`、
`harness_preset`、`harness_fingerprint` 和七类 `harness_*` 列。第一版只执行
`single-agent` / `builtin` orchestration；其他 orchestration 会写 explicit `unsupported`
result，不会静默降级。

### Harness surface 与权限探针

Codex cell 的 `bare-codex` 会物化一个隔离 `CODEX_HOME`，只复制运行必要的 `auth.json`
并重写最小 `config.toml`。runner 随后运行 `codex debug prompt-input`，把摘要写进
`manifest.json` 的 `codex_harness_runtime.probe`：这里只保存 `stdout_sha256`、字节数、
exit code 和 `visibility_flags`，用来确认用户 `AGENTS.md`、skills、MCP、hooks、memory
是否真的没有进入 prompt surface。

权限范围单独用 preflight probe 验证：

```bash
aigdbench probe-permissions \
  --harness-cmd 'codex exec --cd {workspace} {task}' \
  --probe-root ./permission-probe \
  --timeout 120
```

probe 会创建 `probe-root/workspace/` 作为测试目录，并在它旁边创建 outside read/write
canary。通过条件是：harness 能写 `workspace/` 内文件，但不能把 outside secret 泄漏回
workspace，也不能改写 outside canary。结果写入 `permission_probe.json`，失败时命令
exit 1，适合在正式 experiment 前做自动门禁。

这个 probe 是观察式校验：它证明本次 harness 调用没有越过测试目录产生可见读写副作用。
它不替代 OS-level sandbox，也不能证明任意未来命令都被内核强制隔离；强隔离仍由被测
agent / CLI 的 sandbox、workspace、approval 配置负责。

### 并行执行与 attempt 隔离

`aigdbench run --experiment` 的并发度来自 `experiments/execution.yaml`，不通过 CLI 参数覆盖。
当前默认配置：

```yaml
parallelism:
  experiment_jobs: 2
  testcase_jobs: 2
  max_jobs: 4
```

- `experiment_jobs`：同时运行的 cell 数。
- `testcase_jobs`：每个 cell 内同时运行的 testcase attempt 数。
- `max_jobs`：所有 cell 合计的全局 attempt 上限。

每个 testcase attempt 都落在独立目录：

```text
results/<experiment_id>/<cell_id>/attempts/<testcase_id>/
  workspace/
    TASK.md
    ...
  logs/
    harness.log
    events.jsonl
  artifacts/
  result.json
```

被测 agent 的 `cwd` 是 `workspace/`，`{task_file}` 指向 `workspace/TASK.md`。runner 不把
testcase source dir、results root、repo root 或 logs dir 作为 placeholder 暴露给 agent。
`harness.log` 会随着 agent stdout/stderr 实时追加，外层守护 agent 可以 tail 这个文件判断进度；
`events.jsonl` 记录 `running`、`verifying`、`finished` / `error` 状态。

如果 cell 使用 Codex harness，manifest 中的 `codex_harness_runtime.codex_home` 是 cell-level
template；每个 attempt 会再复制出自己的 `attempts/<testcase_id>/codex_home/`，并用这个
`CODEX_HOME` 启动被测 Codex，避免并发 testcase 共享 session/cache/history。

注意：`cwd=workspace` 和最小 env 不是 OS-level sandbox。强隔离要靠被测 agent 自己的限制参数
实现，例如 Codex / Claude Code 的 workspace、sandbox、approval 配置；runner 的责任是只暴露
`workspace` 和 `TASK.md`。

---

## Dashboard 可视化（`aigdbench serve`）

每个 `--report FILE` 都是独立的 JSON 文件。要并排对比多次运行、而不是读原始 JSON，启动本地
dashboard（纯标准库、完全离线、无额外依赖）：

```bash
aigdbench serve --reports-dir . --testcases-dir ./testcases_filtered
# → 打开 http://127.0.0.1:8000
```

它在**每次请求**时扫描 `--reports-dir` 下的 `report*.json`，所以重跑一次基准、刷新页面就能立刻看到新结果。
三个 tab：

- **Reports** —— 一个 report 文件一次运行（按 `harness` + 文件 mtime 标注）：均分柱状图、
  「每 testcase × 每次运行」的分数矩阵、按 category / 时间稳定性的聚合。
  **点任意矩阵格**可下钻到该次运行的逐 check `expected` vs `actual`、harness 日志尾部，
  以及（survey 行）AI 活动记录。
- **Testcases** —— 来自 `--testcases-dir` 的 testcase 库：每个 case 的任务文本、验证器类型、打分模式、文件清单。
- **Contents** —— 直接查看每个 testcase 的文件内容。
- **Run** —— **在网页上手动发起一次正式 benchmark**（默认开启，`--allow-run`）。**跑的是真实的
  docker + Kubernetes 并行**（`scripts/run_k8s_matrix.sh`：一 testcase 一 k8s Job，跑在共享 runner
  镜像上，harness 取自集群 Secret），**不是本机跑**——与 PR 候选流走同一条生产路径。表单里填：
  **run 名称（必填，自定义）**、可选 `testcases`（留空=全部，空格/逗号分隔 id）、可选
  **harness 命令**（留空=用 Secret 里的默认；填写则覆盖每个 Job 的 `HARNESS_CMD`，Secret 仍供 API key，
  `{task}` 由 runner 替换）、`jobs`（k8s 并发上限）、单 testcase `timeout`。点 **Start benchmark**
  （一次只允许一个），**Live status** 每 2 秒轮询显示状态徽章 / 经过秒数 /
  **docker 环境信息**（镜像、namespace、Secret、harness、testcase 数）/ matrix 日志 tail，可随时 **Stop**。
  跑完把聚合出的 `report.json` **打上你的自定义 run 名称**（作为 `harness` 标签）复制进 `--reports-dir`，
  于是和正常流程一样出现在 Reports tab——「正在跑 / 已经跑过」的数据和报告都能实时看到。
- **Status** —— **专门看「当前是否有测试在跑 / 进度到哪了」**。大进度条（已完成 / 总数 + 百分比）、
  状态徽章、经过秒数、docker 环境信息，以及**每个 testcase 的实时格子**（pending → running → pass/fail 及分数，
  随各 k8s Job 结束而点亮）。此外**页头有一个常驻的运行指示灯**（在任意 tab 都可见）：有 run 在跑时脉冲显示
  `running x/y`，跑完显示 `done x/y`。后台每 2 秒（运行中）/ 8 秒（空闲）轮询，无需刷新。

参数：`--testcases-dir` 缺省用 `./testcases`（若存在）；`--port` / `--host` / `--no-open-browser` 可选；
`--editable` 开启后可在 dashboard 内新建/编辑/删除 testcase（默认只读）；
`--allow-run/--no-allow-run` 控制 Run tab（默认开启）；Run tab 的 docker/k8s 参数：
`--runner-image`（默认 `harbor.omgwow.ai/beaver_hub-public/aigdbench-runner:latest`，复用不重建）、
`--k8s-namespace`（默认 `default`）、`--harness-secret`（默认 `aigdbench-harness`）、
`--image-testcases-dir`（默认 `/app/testcases_filtered`）、`--jobs`（默认 16）；
`--webhook-log` 指向 orchestrator 的 `webhooks.jsonl` 以启用 Webhooks tab。

**对外开放端口**（其它机器/公网访问）——把 host 绑到 `0.0.0.0` 并放开防火墙即可：

```bash
aigdbench serve --host 0.0.0.0 --port 8000 \
  --reports-dir ./dashboard_reports --testcases-dir ./testcases_filtered --no-open-browser
# 其它节点访问 http://<本机IP>:8000/
```

> ⚠️ 绑到 `0.0.0.0` + 默认开启的 Run tab **没有鉴权**：能连到该端口的任何人都能发起 benchmark
> 甚至停止运行。仅在可信内网使用，或用 `--no-allow-run` 关掉手动起跑、或在前面加一层反向代理鉴权。
> 启动时若检测到绑定所有网卡会打印一条 WARNING 提醒。

---

## Testcase 数据集

仓库里有**两套** testcase 集合，服务不同目的：

| 目录 | 规模 | 用途 |
|---|---|---|
| `testcases/` | ~519 个（完整挖掘库） | 广度评测、统计显著性、研究真实修复分布 |
| `testcases_filtered/` | **推荐评测集（数量见 manifest）** | 快速冒烟 / 演示 / 开发迭代 / 验证器全链路自检 |

### 完整库 `testcases/`（~519 个）

由四部分构成：

1. **手工代表集**（trap-driven，按「能力 × 难度」矩阵设计）——每个围绕一个具体的 AI 失败模式（trap）构造，
   偷懒实现会踩坑扣分，只有正确改动满分。全部自包含 folder 型。
2. **hard / brutal 加难集**——多 trap 叠加、多文件多系统联动、对抗型隐藏 bug，专门拉开强 harness 的区分度。
3. **真实 AI 失败挖掘集**——用 `scripts/mine_retry_sessions.py` 扫本地 Claude/Codex 对话历史，
   挑出"单次对话反复重试"的真实开发会话，还原根因后重制成 testcase。
4. **survey-fixcommit / survey-\* 挖掘集**（~485 个）——由 sibling 工具从真实游戏 repo 的 git 历史里
   自动挖掘出的问题会话，git 型，用 `survey_bad_case` 验证器回归判定。

### 推荐评测集 `testcases_filtered/`

从完整库里人为**再平衡**挑出的最有代表性、oracle 最鲁棒的 case。**当前数量与分布以自动生成的 [`testcases_filtered_manifest.json`](testcases_filtered_manifest.json) 为单一事实源**（`python scripts/make_manifest.py --check` 同时校验分布约束，应接入 CI）。特点：

- **26 个自包含 folder 型**（任意目录可跑）+ **4 个源自真实 git bug-fix commit 的高区分度 case**（见下）；
- **5 个 category 全覆盖**：behavior_logic ×15、architecture ×6、precise_edit ×4、intent_translation ×3、visual_audio ×2；
- **6 种验证器全覆盖**：`godot_scene_assert` ×17、`survey_bad_case` ×4、`py_config` ×3、`py_gdscript_ast` ×2、`py_tscn_diff` ×2、`visual_static` ×2；
- 全部通过 `aigdbench audit`（noop 0 / golden 1），并带有分级陷阱（部分正确 → 部分分）。

> **4 个 git bug-fix case（`survey-history_*`）**：从 `gdquest-demos/godot-open-rpg` 的真实修复 commit 挖掘而来，比手工 case 更有区分度（真实的资源导入崩溃、组合缺陷）。从 16 个"oracle 有区分度"的候选里筛出——只保留 golden 在真实 Godot 下能干净通过 L0/L1 门禁的 4 个（其余候选撞上项目主场景的既有崩溃，与被测 bug 无关）。为保持**完全离线**又不臃肿仓库，它们不 vendored 整份项目树，而是引用一份**共享项目快照**（`manifest` 里的 `snapshot` 字段 + 各 case 的 `snapshot.json` 记录 repo + base commit）。快照**不入库**，首次运行前本地生成一次：
> ```bash
> python3 scripts/make_snapshots.py --testcases-dir testcases_filtered   # 需联网 clone 一次
> ```
> 生成后这些 case 与其它 folder 型一样纯离线运行。打分用 `scoring.mode="gated"`（改了相关文件且清除 L0/L1 回归→1，否则→0），并复用 `survey_bad_case.json` 内藏 oracle（`good.diff` 为 golden）。

选取标准与逐类代表性例子见 [`docs/filtered_dataset_report.md`](docs/filtered_dataset_report.md)；
完整库分布见 [`docs/full_dataset_report.md`](docs/full_dataset_report.md)。
**manifest schema、五大 category、验证器细节、逐 case 索引**见
[`testcases/README.md`](testcases/README.md)。

### 新建与准入一个 testcase

一个 testcase 只有满足「干净判别器」契约才被接纳：`noop` 得 0、golden patch 得 1、任何部分分对应真实的部分进展。

```bash
# 生成一个自包含 folder 型骨架
aigdbench scaffold --id my-new-case --category behavior_logic \
  --source-project /path/to/godot/project \
  --task "让 NPC 沿生成的路径移动"

# 反复迭代 verifier.gd 与 fix.diff，直到候选干净
aigdbench smoke --testcase my-new-case --godot-binary /path/to/godot

# 门禁整套并写机器可读健康快照
aigdbench audit --testcases-dir ./testcases \
  --godot-binary /path/to/godot \
  --json docs/testcase_health.json
```

### survey bad cases（来自 sibling `survey` 工具）

`survey_bad_cases.json` **不是**由 `aigdbench` 产生的（且不入库，见 `.gitignore`）。它来自 sibling
[AIGameDevCollecter](../AIGameDevCollecter) 的 `survey` CLI，从真实游戏 repo 的 git 历史里挖掘问题 AI 会话
（L0/L1 失败、高人工干预比、多轮才解决），导出成 AIGameDevBench 的 report 格式。这些行由 survey 的规则引擎打分，
没有 `testcase.toml`/baseline/verifier，**不能**被 `aigdbench run` 跑，只在 dashboard 的 Reports 矩阵里出现。

---

## 验证器类型

| type | 需要 Godot | 读取 | 检查什么 |
|---|---|---|---|
| `py_config` | 否 | `expected.json` | 按别名比对数值/配置字段；防照抄守卫 |
| `py_tscn_diff` | 否 | `expected_delta.json` + `baseline/` | 场景节点/属性增删改的差异，且无副作用 |
| `py_gdscript_ast` | 否 | `arch_rules.json` | import/extends/路径约束，按权重扣分 |
| `godot_scene_assert` | 是 | `verifier.gd` + `verifier_scene.tscn` | 场景模式启动项目（支持 autoload），运行时断言 |
| `godot_scenetree` | 是 | `verifier.gd` | headless SceneTree 运行时断言 |
| `visual_static` | 是 | `verifier.gd` | 结构化布局断言（`--script` 模式，自行加载场景） |
| `interaction_routing` | 是 | `verifier.gd` | 点击路由 / 焦点顺序 |

细节（每种验证器的打分梯度、`survey_bad_case` 回归判定等）见 [`testcases/README.md`](testcases/README.md)。

---

## 命令速查

```bash
# 列出 testcase
aigdbench list --testcases-dir ./testcases_filtered

# 校验一个候选 testcase 的准入门禁
aigdbench smoke --testcase gdb-task_0002 --godot-binary /path/to/godot

# 门禁整套并更新健康快照
aigdbench audit --godot-binary /path/to/godot --json docs/testcase_health.json

# 基线（应 0）与黄金 fix（应 1），用于调试
aigdbench run --testcase gdb-task_0002 --driver noop
aigdbench run --testcase gdb-task_0002 --driver patch --patch ./testcases/gdb-task_0002/fix.diff

# 用真实 harness 评测整个（推荐）集合，写报告
aigdbench run --testcases-dir ./testcases_filtered --driver command \
  --harness-cmd 'claude -p {task} --dangerously-skip-permissions' \
  --harness my-claude-code --timeout 900 --jobs 4 --repeat 3 \
  --log-dir ./harness-logs --workspace-root ./bench-workspaces \
  --report ./report.json

# 可视化并对比所有 report*.json
aigdbench serve --reports-dir . --testcases-dir ./testcases_filtered

# 统计对比两个 harness（配对 bootstrap：均分差 + 95% CI + p 值）
aigdbench compare --report-a report_claude.json --report-b report_codex.json
```

### 统计显著性对比（`aigdbench compare`）

两个 report 跑同一套 testcase 后，`compare` 在**共同 case** 上做**配对 bootstrap**——对每个 case 的分数差
`d_i = A_i − B_i` 重采样求分布——输出均分差、95% 置信区间、双尾 p 值，把「A 比 B 强」从目视变成可判定：

```
--- compare: A=claude  vs  B=codex  (10 shared testcase(s))
  A mean 0.843   B mean 0.105
  mean diff (A-B): +0.738  [95% CI +0.527, +0.909]  p=0.0000
  verdict: significant (CI excludes 0); point estimate favors claude
  per-category mean diff (A-B): behavior_logic +0.738 (n=10)
  top 5 contributing testcase(s): gdb-task_0002 +1.000 ...
```

配对（同 case 求差）消掉了 case 间难度方差，比直接比两个独立均值敏感得多。`--iters` 控制重采样次数、
`--seed` 保证可复现;还会按 category 拆分差异、并列出贡献最大的 top-N testcase，定位差异来源。
配合 `--repeat` 跑出的稳定分数一起用最佳。

---

## 脚本一览（`scripts/`）

| 脚本 | 作用 |
|---|---|
| `run_bench50.sh` | 用 `claude -p` 跑整套 `testcases_filtered/`，结果/代码/日志落在 `bench_runs/` |
| `run_k8s_matrix.sh` | 构建/推送 runner 镜像，一 testcase 一 k8s Job 并行 fan-out，聚合 `report.{json,md}` |
| `build_runner_image.sh` | 构建含 **claude CLI + 最新 agentic-game-development 插件**的 runner 镜像 |
| `bench-orchestrator.sh` | 常驻服务：监听 GitHub webhook，**仅 PR opened** 事件触发一次 gated 候选 bench（见下方「Webhook 自动触发」） |
| `bench-candidate.sh` | 把一个 PR head 隔离到候选镜像上 bench，**严格优于历史最高分**才自动合并 + 发布 |
| `compare-and-maybe-release.sh` | 候选/批次完成后对比插件基线，达标则 bump 版本并发布 |
| `import_gamedevbench.py` | 把 GameDevBench 的自包含 task 导入成本仓 folder 型 testcase |
| `mine_retry_sessions.py` | 扫本地 Claude/Codex 对话历史，挖掘"反复重试"的真实开发会话 |
| `audit_testcases.py` | 门禁整套 testcase，写机器可读健康快照 |
| `_*.py` | 数据集统计 / oracle 强度分析 / survey 任务脱敏改写等一次性辅助脚本 |

---

## Webhook 自动触发：PR opened → gated 候选 → 自动合并/发布

`agentic-game-development` 仓**新开一个 PR** 时（GitHub `pull_request` 事件、
`action=opened`），自动把该 PR 的 head commit 隔离到一个**候选镜像**上跑一次全量并行
benchmark（每个 testcase 一个 k8s Job，harness = **claude + 候选插件**）。只有当候选成绩
**严格优于 main 历史最高分**时，才自动 `gh pr merge` 进 main 并发布新 release；不达标的 PR
不进 main。main 成为 benchmark 守护的受保护分支（完整设计见
[`docs/spec-v2.md`](docs/spec-v2.md)）。

> **只在 PR opened 触发。** `synchronize`（PR 有新 push）/ `reopened` / `ready_for_review`
> 以及所有 `push` 事件都**不触发** benchmark——orchestrator 收到后回 `202 {skipped:...}` 忽略。
> 如需对已开 PR 的更新重新评测，关闭再重新开一个 PR。

### 端到端流程

```text
agentic-game-development 仓 PR opened
  └─▶ https://hook.omgwow.tech/github            (GitHub webhook)
        └─▶ github-webhook receiver (k8s webhook ns，已部署)
              │  ① 校验 HMAC；② pull_request 事件
              │  ③ fire-and-forget 转发（不阻塞、照常回 202）
              ▼
        POST $BENCH_TRIGGER_URL/trigger
          {delivery, repo, event:"pull_request", action:"opened",
           pr_number, head_sha, base_ref}
              └─▶ bench-orchestrator.sh（常驻本机，tmux）
                    ① x-bench-token 校验；action≠opened / 非 PR → 忽略
                    ② 按 head.sha 去重（key = pr-<num>-<sha>）
                    ③ bench-candidate.sh --commit <head_sha> --pr <num> ...：
                          · checkout PR head 到候选分支 bench-<sha>
                          · 打临时版本 <ver>-bench.<sha8> + vendor 候选插件
                          · build → push 不可变 tag image:<sha>
                          · run_k8s_matrix.sh 一 testcase 一 Job 并行跑
                          · 聚合 report.json（记 trigger + plugin_change + mean_score）
                    ④ 门禁：mean_score > baseline.best_score（main 历史最高）？
                          ├ 否 → 丢弃候选、保留 report、给 PR 评论分数、结束
                          └ 是（且 --auto-release）↓
                    ⑤ gh pr merge <num> --squash → 基于 main 重打包（去 -bench 后缀 +
                       semver bump）→ 刷新 baseline.best_score → push main
                    ⑥ release-on-bump.yml 检测版本变更 → 自动发布 release
                       （release notes 贴 PR 来源 + 测试数据）
```

每次触发的产物落在 `results/<delivery>/`（含 `report.json` 的 `trigger` /
`plugin_change` 字段、`report.md`、各 pod 日志、`candidate.log`、`release.log` 等）。

### 前置准备（各做一次）

1. **runner 镜像**（含 claude + 插件）—— **走 registry**：
   ```bash
   scripts/build_runner_image.sh -i harbor.omgwow.ai/beaver_hub-public/aigdbench-runner:latest --push
   ```
   `beaver_hub-public` 是 public 项目，**pod 无需 imagePullSecret** 即可拉取。候选流每次会为
   PR head 另建一个不可变 `:<sha>` tag，避免 `:latest` 缓存陈旧问题。

2. **harness 凭证 Secret**（default ns）：
   ```bash
   kubectl -n default create secret generic aigdbench-harness \
     --from-literal=HARNESS_CMD='claude -p {task} --dangerously-skip-permissions --plugin-dir /opt/agd-plugin' \
     --from-literal=ANTHROPIC_API_KEY=sk-... \
     --from-literal=IS_SANDBOX=1          # 容器以 root 运行时，claude 需要它才允许 --dangerously-skip-permissions
   ```
   （网关部署可再加 `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`。）

   > **裸 claude（不带 agentic-game-development 插件/skills）**：镜像把插件装了两条路径 ——
   > `/opt/agd-plugin`（仅 `--plugin-dir` 显式加载）**和** `$HOME/.claude/skills`（`HOME=/tmp`，
   > `claude -p` 按 description **自动加载，无需任何 flag**）。所以**只去掉 `--plugin-dir` 不够**，
   > 还得把 `HOME` 指到一个干净目录切断第二条自动加载路径：
   > ```bash
   > kubectl -n default create secret generic aigdbench-harness \
   >   --from-literal=HARNESS_CMD='env HOME=/tmp/bare-claude claude -p {task} --dangerously-skip-permissions' \
   >   --from-literal=ANTHROPIC_API_KEY=sk-... \
   >   --from-literal=IS_SANDBOX=1
   > ```
   > `env HOME=…` 只改 `HOME`，Secret 里的 `ANTHROPIC_API_KEY` 等仍照常传给 claude。
   > 跑一个 case 用 `--harness-format stream-json` 看 report 的 `ai_agent_context`，
   > 确认没有 game-dev skill 加载即为真裸。

3. **receiver 转发**：由 receiver 镜像作者按 [`docs/webhook-forward-contract.md`](docs/webhook-forward-contract.md)
   订阅 `pull_request` webhook、转发 `event/action/pr_number/head_sha/base_ref` 等字段，
   并配置两个 env（`BENCH_TRIGGER_URL` / `BENCH_TRIGGER_TOKEN`）；部署侧按
   `../winter-update-runbook.md` 更新镜像并 `kubectl -n webhook set env` 注入。

### 启动常驻 webhook 端口监控（tmux）

监听 receiver 转发的 `POST /trigger`：

```bash
scripts/bench-orchestrator.sh --mode http --port 8899 --token <shared-token> \
  --image harbor.omgwow.ai/beaver_hub-public/aigdbench-runner:latest \
  --image-repo harbor.omgwow.ai/beaver_hub-public/aigdbench-runner \
  --secret aigdbench-harness --jobs 16 \
  --testcases-dir /app/testcases_filtered \
  --local-testcases-dir ./testcases_filtered \
  --plugin-repo ../agentic-game-development \
  --auto-release --bump patch
```

- `--mode http`：在 `0.0.0.0:$PORT` 起一个零依赖 HTTP 服务，收 receiver 转发的
  `POST /trigger`；`GET /healthz` 返回 `{"ok":true}` 可做连通性探活。
- `--token`：与 receiver 侧共享的 `x-bench-token`，校验来源防误触（不传则不校验，仅测试用）。
- `--image-repo`：候选镜像仓（不带 tag），候选流会追加不可变 `:<sha>` tag。
- `--auto-release`：**打开**才会真正 `gh pr merge` + bump + push；不加则跑到门禁为止、只
  上报候选是否达标、不改动 main（安全默认）。

### Webhook 接收与查看（dashboard 的 Webhooks tab）

`scripts/start_dashboard.sh` 会自动起一个 **webhook 接收器**（`scripts/webhook_receiver.py`，默认
`0.0.0.0:8899`），接住 k8s github-webhook receiver 转发来的 `POST /trigger`
（`BENCH_TRIGGER_URL=http://<本机>:8899/trigger`），把每一条**完整**追加到 JSONL
（默认 `.orchestrator/webhooks.jsonl`，全量长期保存）。若这个端口没有进程在听，转发会
`fetch failed` 而丢弃——这正是「触发了 webhook 但页面为空」的根因。

- **自动触发 benchmark**（`WEBHOOK_AUTORUN_MODE`，默认 `candidate`）：接收器收到 `action=opened`
  的 PR 时——
  - `candidate`（**默认，推荐**）：跑 `scripts/bench-candidate.sh`，它把插件按
    **`origin/main` + cherry-pick(PR commit)** 重建（= **PR 合并进 main 之后的插件**），
    构建候选镜像 → 一 testcase 一 k8s Job 评测 → 记 `report.json`。默认带
    `--auto-release`（`WEBHOOK_AUTO_RELEASE=1`）：候选**严格优于历史最高分**才 `gh pr merge` +
    version bump + push main（release-on-bump 发新版）；否则只上报不动 main。一次只跑一个候选，
    同一 PR head 去重。跑完把 report 复制进 `--reports-dir`（`harness=pr-<号>-<sha8>`），出现在 Reports tab。
  - `matrix`：POST dashboard 的 `/api/runs/start`，用**固定 `:latest` 镜像**跑全部 testcase
    （**不反映 PR 的插件改动**，仅用于快速冒烟）。
  - `off`：只记录不执行。
  > 说明：harness 的「secret default」指 k8s Secret `aigdbench-harness` 里的 `HARNESS_CMD`
  > （`claude -p {task} --plugin-dir /opt/agd-plugin` + `ANTHROPIC_API_KEY` 等）。因含密钥且面板
  > 无鉴权公开，故 status 只显示 `(secret default)` 不回显内容。
- **查看**：dashboard 的 **Webhooks tab** 每 3 秒自动刷新，每条显示**判定**
  （accepted / skipped+原因 / error）、event/action、delivery id、来源 IP、时间，点开看完整 body。
  默认展示最新 200 条；点 **Load all** 加载全部（`GET /api/webhooks?limit=all`，响应含 `total`）。

```bash
# 一键：dashboard + 接收器(8899) 一起起，Webhooks tab 直接可见
scripts/start_dashboard.sh
# 手动指定日志（不带接收器时）：
aigdbench serve --host 0.0.0.0 --port 8000 --webhook-log ./.orchestrator/webhooks.jsonl --no-open-browser
```

> bench-orchestrator.sh 的 http/watch-logs 模式也会写同一个 `webhooks.jsonl`；两者择一即可。

探活与手动触发（本机自测）：

```bash
curl -s localhost:8899/healthz                       # -> {"ok":true}
curl -s -XPOST localhost:8899/trigger \
  -H 'x-bench-token: <shared-token>' -H 'content-type: application/json' \
  -d '{"delivery":"manual-1","repo":"o/agentic-game-development",
       "event":"pull_request","action":"opened",
       "pr_number":"42","head_sha":"<sha>","base_ref":"main"}'
```

### 无需改 receiver 的 fallback

若 receiver 到本机网络不通、或 receiver 暂不转发，用 `--mode watch-logs`：orchestrator 直接
tail receiver pod 日志（凭 `pods/log` 权限），对每条 `accepted GitHub webhook delivery`
中 `action=opened` 的 `pull_request` 行触发同一套候选流（按 head.sha 去重）：

```bash
scripts/bench-orchestrator.sh --mode watch-logs \
  --webhook-kubeconfig ~/mc-winter-zhao-kubeconfig --webhook-ns webhook \
  --image ... --image-repo harbor.omgwow.ai/beaver_hub-public/aigdbench-runner \
  --secret aigdbench-harness --plugin-repo ../agentic-game-development --auto-release
```

### 门禁与发布判定

候选流读取 `agentic-game-development/workflow/benchmark-baseline.json` 里的 `best_score`
（main 历史最高分）与 `best_version` 作为门禁基准：

- **候选 mean_score ≤ best_score**：不达标——不合并 PR，仅回填一条 comment（分数/对比），
  保留 report，结束。
- **候选 mean_score > best_score 且 `--auto-release`**：`gh pr merge <num> --squash` 进
  main → 去掉 `-bench` 后缀重打包 + semver bump → 刷新 `best_score`/`best_version` →
  push main → `release-on-bump.yml` 自动发布新 release（notes 贴 PR 来源 + 测试数据）。
- **未加 `--auto-release`**：跑到门禁为止，只报告是否达标，不写 main。

> `report.json` 的 `plugin_change` 字段记录本次候选的 `commit` / `branch` /
> `changed_files` / `log` / `diff`，便于在 dashboard 或 PR 里核对「跑的是哪版插件、改了什么」。

---

## 运行测试

```bash
pytest
```

---

## 开发者文档

面向开发者的完整参考——整体架构、一次评测的生命周期、testcase 数据模型、
driver / 验证器 / L0-L1 门、dashboard 与 webhook 链路、k8s/本地矩阵执行，
以及**关键路由与代码路径速查**，见 [`docs/dev.md`](docs/dev.md)。

## BeaverHub 运行

除了本地 / k8s 矩阵脚本，一个 testcase 也可以作为 **BeaverHub** 平台任务来跑：
把 `docker/entrypoint.sh` 的隐式合同显式化成一份 TaskPackage（同一份 runtime +
合同可复用于 Interactive / Run / Batch 等 RunType，无需为每种形态手写编排）。
契约、运行命令、集群前置条件、以及已实测跑通的证据见
[`.beaver/tasks/aigdbench/README.md`](.beaver/tasks/aigdbench/README.md)。
