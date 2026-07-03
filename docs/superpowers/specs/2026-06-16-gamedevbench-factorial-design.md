# GameDevBench Factorial Benchmark Design

## 背景

现有 GameDevBench runner 把 `agent`、`model`、`use_mcp`、`use_runtime_video` 和 `run_name` 作为主要变量。这个结构适合复现原论文结果，但不适合我们后续要做的三类比较：

- 相同 harness 下，不同 model 的 game-dev 能力。
- 相同 model 下，不同 custom harness 的能力。
- 相同 model / harness / task set 下，不同 multi-agent orchestration 对结果的影响。

问题不在字段数量，而在变量混在一起。现有 `agent` 同时承担 agent CLI、工具配置、提示策略的一部分含义；`use_mcp` 和 `use_runtime_video` 又是 harness 组件，但被写成两个独立布尔值。后续如果继续往 CLI 上加布尔参数，AB test 会很快失去可比性。

## 目标

第一版改造要把 benchmark 的实验变量拆成五个正交维度：

```text
model × agent_cli × orchestration × task_set × harness_components
```

这五个维度里，`harness_components` 是 custom harness 的底层真源。`harness_preset` 只是人类可读的 shortcut，不作为科学比较的底层变量。

第一版同时支持两种入口：

- 配置驱动：用 experiment YAML 声明多个 cell，批量跑矩阵。
- CLI 单 cell：用命令行直接跑一个组合，便于 smoke、调试和小规模 AB。

## 非目标

第一版不实现完整 multi-agent workflow runtime。`single-agent` 继续复用现有 solver；其他 orchestration 可以进入 schema，但运行时先返回 unsupported，等后续单独实现。

第一版也不改 hidden evaluator、leaderboard 算法、任务内容和 Godot validation 逻辑。它们属于 benchmark infrastructure，不属于 agent harness。

## 核心概念

### Cell

`cell` 是最小实验单元。每个 cell 固定一组变量：

```yaml
id: gpt54_codex_gd_full_single_smoke
model: gpt-5.4
agent_cli: codex
orchestration:
  id: single-agent
task_set: smoke
harness:
  preset: gd-full
```

如果两个 cell 要比较 model，就必须保持 `agent_cli`、`orchestration`、`task_set` 和 resolved `harness_components` 完全一致。比较 harness 时，只允许 resolved `harness_components` 改变。比较 orchestration 时，只允许 orchestration spec 改变。

### Agent CLI

`agent_cli` 是执行器家族，例如：

- `codex`
- `claude-code`
- `gemini-cli`
- `openhands`
- 未来可能加入的 `pi-agent`

旧参数 `--agent` 保留为兼容 alias。内部和结果里都写入 `agent_cli`；短期为了兼容旧结果，也保留 `agent` 字段。

### Custom Harness

custom harness 按实际载体类型分类，不按目的分类。只保留七类：

```yaml
custom_harness:
  instruction_files: []
  skills: []
  mcp_servers: []
  hooks: []
  cli_config: []
  tool_wrappers: []
  context_artifacts: []
```

判定标准：

```text
agent 能看见、能调用、会被注入、或会改变它工具 / 权限 / 上下文面的 artifact，才算 custom harness。
```

细节：

- `instruction_files` 包括 `CLAUDE.md`、`AGENTS.md`、task-local overlay、subagent description markdown。
- `skills` 包括 skill package 本体，以及 skill 内的 `scripts/`、`templates/`、`assets/`。
- `mcp_servers` 包括 MCP server 注册、工具、resources 和 resource templates。
- `hooks` 包括 pre-tool、post-tool、session start/end、notification、auto-context、auto-verify hook。
- `cli_config` 包括 agent CLI config、permission、feature flag、MCP registration、sandbox/approval policy。
- `tool_wrappers` 包括 agent 可调用的 executable wrapper、shim、runner、parser、probe。
- `context_artifacts` 包括 docs、memory、repo summary、API notes、failure notes、task briefing。这里的前提是它们会被注入或暴露给 agent。

不进入 custom harness 的内容：

- `agent_cli` 本体。
- hidden evaluator、leaderboard、result aggregation、result schema。
- agent 看不见也不能调用的 retrieval index 或内部缓存。

### Harness Preset

底层真源是组件组合。named preset 只用于直观调用：

```yaml
harness_presets:
  gd-baseline:
    components:
      instruction_files: []
      skills: []
      mcp_servers: []
      hooks: []
      cli_config: []
      tool_wrappers: []
      context_artifacts: []

  gd-full:
    components:
      instruction_files: [gd-agents-md]
      skills: [godot-debugging, game-dev-verification]
      mcp_servers: [godot-screenshot]
      hooks: [session-start-context]
      cli_config: [codex-enable-mcp]
      tool_wrappers: [godot-runner, godot-visual-probe]
      context_artifacts: [godot-api-notes, prior-failure-memory]
```

Experiment cell 可以引用 preset，也可以 add/remove component：

```yaml
cells:
  - id: gpt54_codex_gd_full_single_smoke
    model: gpt-5.4
    agent_cli: codex
    orchestration:
      id: single-agent
    task_set: smoke
    harness:
      preset: gd-full

  - id: gpt54_codex_gd_full_no_hooks_single_smoke
    model: gpt-5.4
    agent_cli: codex
    orchestration:
      id: single-agent
    task_set: smoke
    harness:
      preset: gd-full
      remove:
        hooks: [session-start-context]
```

Runner 执行前必须 resolve 出 effective harness，并计算 fingerprint：

```json
{
  "harness_preset": "gd-full",
  "harness_components": {
    "instruction_files": ["gd-agents-md"],
    "skills": ["godot-debugging", "game-dev-verification"],
    "mcp_servers": ["godot-screenshot"],
    "hooks": ["session-start-context"],
    "cli_config": ["codex-enable-mcp"],
    "tool_wrappers": ["godot-runner", "godot-visual-probe"],
    "context_artifacts": ["godot-api-notes", "prior-failure-memory"]
  },
  "harness_fingerprint": "sha256:..."
}
```

Fingerprint 输入应是规范化后的 JSON：key 排序、数组排序、去重。后续如果 component registry 记录了 path、version、content hash，fingerprint 再包含这些 resolved metadata。

### Orchestration

`orchestration` 不能只是一段 label。它至少要记录两件事：

- workflow source：编排从哪里来。
- workflow shape：执行拓扑和阶段。

我们先支持两类 source：

```yaml
orchestration:
  id: single-agent
  source_type: builtin

orchestration:
  id: agentic-workflow
  source_type: agentic_workflow
  generator_agent_cli: claude-code
  generator_model: claude-sonnet-4-5
  workflow_prompt: workflows/generate_godot_workflow.md

orchestration:
  id: scripted-parallel-verify
  source_type: scripted_workflow
  workflow_file: workflows/parallel_verify.yaml
```

`agentic_workflow` 的含义是：运行时由一个 agent 写代码或生成 workflow spec，再由 orchestrator 执行。它参考 Claude Code dynamic workflow 的 agentic 方向，重点测“模型是否会自己设计合适的编排”。

`scripted_workflow` 的含义是：人类预先写好编排脚本，runner 按脚本执行。编排语言和设计可以接近 dynamic workflow，但生成权在 benchmark 作者手里。它重点测“固定编排策略本身是否提升 game-dev bench”。

第一版只真正执行：

```yaml
orchestration:
  id: single-agent
  source_type: builtin
```

其他 source_type 先进入 schema 和 manifest。如果用户请求执行未实现 workflow，runner 应明确返回 unsupported，而不是静默退化成 single-agent。

## Experiment YAML

建议第一版文件结构：

```yaml
id: gd-harness-ab
description: Codex harness AB smoke test

task_sets:
  smoke:
    task_list: test_task.yaml

harness_presets:
  gd-baseline:
    components:
      instruction_files: []
      skills: []
      mcp_servers: []
      hooks: []
      cli_config: []
      tool_wrappers: []
      context_artifacts: []

cells:
  - id: gpt54_codex_baseline_single_smoke
    model: gpt-5.4
    agent_cli: codex
    orchestration:
      id: single-agent
      source_type: builtin
    task_set: smoke
    harness:
      preset: gd-baseline
```

## CLI

配置驱动：

```bash
uv run python gamedevbench/src/benchmark_runner.py run \
  --experiment experiments/gd-harness-ab.yaml
```

单 cell：

```bash
uv run python gamedevbench/src/benchmark_runner.py \
  --agent-cli codex \
  --model gpt-5.4 \
  --harness gd-full \
  --orchestration single-agent \
  run --task-list test_task.yaml
```

兼容旧入口：

```bash
uv run python gamedevbench/src/benchmark_runner.py \
  --agent codex \
  --model gpt-5.4 \
  run --task-list test_task.yaml
```

兼容模式下，`--agent` 等价于 `--agent-cli`。如果同时传入两者且值不一致，runner 应报错。

## 结果目录和 Manifest

结果目录改为 cell 隔离：

```text
results/<experiment_id>/<cell_id>/
  manifest.json
  final_results.json
  final_results.csv
  progress_<agent_cli>_<model>.json
```

`manifest.json` 是复现真源：

```json
{
  "experiment_id": "gd-harness-ab",
  "cell_id": "gpt54_codex_gd_full_single_smoke",
  "model": "gpt-5.4",
  "agent_cli": "codex",
  "orchestration": {
    "id": "single-agent",
    "source_type": "builtin"
  },
  "task_set": "smoke",
  "task_list_file": "test_task.yaml",
  "tasks": ["task_0002"],
  "harness_preset": "gd-full",
  "harness_components": {
    "instruction_files": ["gd-agents-md"],
    "skills": ["godot-debugging", "game-dev-verification"],
    "mcp_servers": ["godot-screenshot"],
    "hooks": ["session-start-context"],
    "cli_config": ["codex-enable-mcp"],
    "tool_wrappers": ["godot-runner", "godot-visual-probe"],
    "context_artifacts": ["godot-api-notes", "prior-failure-memory"]
  },
  "harness_fingerprint": "sha256:...",
  "git_commit": "...",
  "runner_version": "0.1.0",
  "started_at": "..."
}
```

每条 task result 也复制这些字段，方便直接从 CSV groupby：

- `experiment_id`
- `cell_id`
- `model`
- `agent_cli`
- `orchestration_id`
- `orchestration_source_type`
- `task_set`
- `harness_preset`
- `harness_fingerprint`
- 七类 `harness_*` component 列

## 第一版实现计划边界

第一版应做：

- 新增 experiment YAML parser。
- 新增 `--agent-cli`，并让旧 `--agent` 作为 alias。
- 新增 `--harness`、`--orchestration`、`--experiment`。
- 新增 harness preset resolve 和 component fingerprint。
- 新增 `manifest.json`。
- 扩展 final JSON / CSV / per-task result 字段。
- `single-agent` 复用现有 solver。
- 非 `single-agent` orchestration 明确 unsupported。

第一版不做：

- 真正的 dynamic workflow runtime。
- agentic workflow generator。
- workflow 脚本执行器。
- component registry 的 content hash 追踪。第一版 fingerprint 先基于 component ID 组合。

## Codex Harness AB 的当前约定

本项目先把 Codex harness AB 的运行边界固定下来，避免 `--harness` 只写 metadata，却不改变真实 agent surface。

固定变量：

- `agent_cli = codex`
- `model = gpt-5.5`
- `model_reasoning_effort = low`
- `orchestration = single-agent`
- provider 沿用当前 Codex 的 built-in `openai` provider + `openai_base_url`，不切 custom provider，避免把 provider 差异混进 harness 对比。

对比的两个 harness preset：

- `bare-codex`：隔离 `CODEX_HOME`，共享必要的 auth/provider 基础配置，但不加载 user `AGENTS.md`、user skills、自定义 MCP、hooks、memory/context artifacts。Codex CLI 自带的最低限度 system prompt 不算 custom harness。
- `custom-current-full`：从当前 `~/.codex` 快照 materialize 出 user `AGENTS.md`、Codex enabled user skills、live enabled MCP、hooks、memory/context artifacts 和相关 CLI feature config。当前 live 组件以 `codex debug prompt-input` 和 cc-switch/Codex config 探针为准。

Runner 执行 Codex cell 前必须 materialize 独立 `CODEX_HOME`：

```text
~/.codex-bench/homes/<harness>-<effort>-<fingerprint-prefix>/
```

`manifest.json` 必须写入 `codex_harness_runtime`，至少包含：

- isolated `CODEX_HOME` path
- harness preset / fingerprint
- `model_reasoning_effort`
- `codex debug prompt-input` 的 probe 摘要

probe 只保存 `stdout_sha256`、字节数和 visibility flags，不保存完整 prompt。这样能证明隔离是否生效，又不把 memory 或用户级 instruction 原文扩散到 benchmark artifact。

当前 2x2 smoke experiment 放在：

```text
experiments/codex-harness-ab-2x2-smoke.yaml
```

它覆盖：

```text
bare-codex × gameplay_logic_smoke
custom-current-full × gameplay_logic_smoke
bare-codex × 2d_graphics_animation_smoke
custom-current-full × 2d_graphics_animation_smoke
```

注意：仓库当前没有权威的 `task_id -> taxonomy` metadata。README 只有四类比例，`tasks.yaml` 只有 task id，`task_config.json` 没有 category 字段。因此 smoke task set 是人工 seed，用来验证 harness isolation 和结果 schema；正式 full category run 需要先补 `task_metadata.yaml/json`，再从 metadata 生成 `gameplay_logic` 和 `2d_graphics_animation` task list。

## 验证断言

实现阶段至少要验证：

- 旧 CLI `--agent codex ... run --task-list test_task.yaml` 仍能构造 runner，不因新字段破坏兼容。
- 新 CLI 单 cell 能把 `agent_cli`、`orchestration`、`harness_preset`、resolved components 和 fingerprint 写进结果。
- Experiment YAML 能展开至少两个 cell，并为每个 cell 使用独立结果目录。
- 改变一个 harness component 会改变 `harness_fingerprint`。
- 未实现 orchestration 不会静默退化成 single-agent。
- Codex harness preset 不只写 metadata：`manifest.json` 里的 prompt probe 必须显示 `bare-codex` 与 `custom-current-full` 的模型可见面不同。
