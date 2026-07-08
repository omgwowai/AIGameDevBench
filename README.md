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
- [运行测试](#运行测试)

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

- harness 输出**实时打印到屏幕**（带 testcase id 前缀），卡住的提示第一时间可见；`--no-stream` 关闭。
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

> **示例脚本**：`scripts/run_bench50.sh` 是一个用 `claude -p` 跑整套 `testcases_filtered/`
> 的完整脚本，结果与日志都落在 `bench_runs/` 下。建议在 `tmux` 里运行以便断开重连。

**手动回路**（不想用 command driver 时）：手工完成任务 → `git diff > ai.diff` →
`aigdbench run --driver patch --patch ai.diff` 出分。

---

## 正交实验（Experiment YAML）

`aigdbench run --experiment` 用配置文件批量跑正交 cell。每个 cell 固定一组变量：

```text
model × agent_cli × orchestration × task_set × harness_components
```

`task_set` 指向 AIGameDevBench testcase 集合；集合列表集中维护在
`experiments/testsets.yaml`，experiment YAML 通过 `test_set_file: testsets.yaml` 引用它。
当前内置集合：

- `filtered_30`：`testcases_filtered/` 的 30 个精选 strong-oracle case。
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

参数：`--testcases-dir` 缺省用 `./testcases`（若存在）；`--port` / `--host` / `--no-open-browser` 可选；
`--editable` 开启后可在 dashboard 内新建/编辑/删除 testcase（默认只读）。

---

## Testcase 数据集

仓库里有**两套** testcase 集合，服务不同目的：

| 目录 | 规模 | 用途 |
|---|---|---|
| `testcases/` | ~519 个（完整挖掘库） | 广度评测、统计显著性、研究真实修复分布 |
| `testcases_filtered/` | **30 个（推荐评测集）** | 快速冒烟 / 演示 / 开发迭代 / 验证器全链路自检 |

### 完整库 `testcases/`（~519 个）

由四部分构成：

1. **手工代表集**（trap-driven，按「能力 × 难度」矩阵设计）——每个围绕一个具体的 AI 失败模式（trap）构造，
   偷懒实现会踩坑扣分，只有正确改动满分。全部自包含 folder 型。
2. **hard / brutal 加难集**——多 trap 叠加、多文件多系统联动、对抗型隐藏 bug，专门拉开强 harness 的区分度。
3. **真实 AI 失败挖掘集**——用 `scripts/mine_retry_sessions.py` 扫本地 Claude/Codex 对话历史，
   挑出"单次对话反复重试"的真实开发会话，还原根因后重制成 testcase。
4. **survey-fixcommit / survey-\* 挖掘集**（~485 个）——由 sibling 工具从真实游戏 repo 的 git 历史里
   自动挖掘出的问题会话，git 型，用 `survey_bad_case` 验证器回归判定。

### 推荐评测集 `testcases_filtered/`（30 个）

从完整库里人为**再平衡**挑出的 30 个最有代表性、oracle 最鲁棒的 case。特点：

- **全部自包含 folder 型**，任意目录可跑；
- **5 个 category 全覆盖**：behavior_logic ×19、precise_edit ×4、intent_translation ×3、architecture ×2、visual_audio ×2；
- **5 种验证器全覆盖**：`godot_scene_assert` ×21、`py_config` ×3、`py_gdscript_ast` ×2、`py_tscn_diff` ×2、`visual_static` ×2；
- 全部通过 `aigdbench audit`（noop 0 / golden 1），并带有分级陷阱（部分正确 → 部分分）。

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
  --harness my-claude-code --timeout 900 \
  --log-dir ./harness-logs --workspace-root ./bench-workspaces \
  --report ./report.json

# 可视化并对比所有 report*.json
aigdbench serve --reports-dir . --testcases-dir ./testcases_filtered
```

---

## 脚本一览（`scripts/`）

| 脚本 | 作用 |
|---|---|
| `run_bench50.sh` | 用 `claude -p` 跑整套 `testcases_filtered/`，结果/代码/日志落在 `bench_runs/` |
| `import_gamedevbench.py` | 把 GameDevBench 的自包含 task 导入成本仓 folder 型 testcase |
| `mine_retry_sessions.py` | 扫本地 Claude/Codex 对话历史，挖掘"反复重试"的真实开发会话 |
| `audit_testcases.py` | 门禁整套 testcase，写机器可读健康快照 |
| `_*.py` | 数据集统计 / oracle 强度分析 / survey 任务脱敏改写等一次性辅助脚本 |

---

## 运行测试

```bash
pytest
```
