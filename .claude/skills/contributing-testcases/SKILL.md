---
name: contributing-testcases
description: >-
  当外部贡献者想给 AIGameDevBench 仓库**提交一个新 testcase 的 PR** 时使用：讲清 testcase 的
  目录格式与 testcase.toml schema，并给出从 fork → 建 case → 本地自测（noop 0 / golden 1 +
  audit）→ 开 PR 到 omgwowai/AIGameDevBench → 过 CI/评审 的**全链路**。触发面：「我想贡献一个
  testcase」「怎么给 AIGameDevBench 提 PR 加用例」「新增 testcase 的格式和提交流程是什么」。
  本 skill 面向「提交流程 + 格式速查」；testcase 的深层设计纪律（golden 改动、oracle 收紧、
  漏分基线的修法）见姊妹 skill authoring-testcases。不用于手感/平衡/纯视觉这类无结构化信号的任务。
metadata:
  expose: [codex, claude]
---

# contributing-testcases

给 **AIGameDevBench**（`omgwowai/AIGameDevBench`）贡献一个新 testcase 并提 PR 的完整指南。

一个 testcase = **(一个起点状态) + (一段自然语言 task) + (一个冻结的黄金验证器)**。它只有满足
**干净判别器**契约才会被接纳：**什么都不做 → 0 分，正确改动 → 满分**，中间质量给中间分。
你的 PR 能不能合并，取决于它是否通过 `aigdbench audit`（noop=0 且 golden=1）。

> 本 skill 讲**格式 + 提交全链路**。写 oracle / 修漏分基线 / 变异播种等**设计纪律**在姊妹 skill
> [`authoring-testcases`](../authoring-testcases/SKILL.md) 里，遇到「noop 拿到分」「golden 不满分」
> 时去那里找修法。

## 全链路一览

```
0. 装好环境（pip install -e ".[dev]"，运行时验证器还需 godot 在 PATH）
1. fork omgwowai/AIGameDevBench，克隆你的 fork，建分支
2. 生成骨架：aigdbench scaffold --id <你的id> --category <类> ...
3. 填 task / 起点 baseline/ / 验证器 oracle / testcase.toml / fix.diff
4. 本地自测直到干净：noop→0、golden→1（aigdbench smoke / audit）
5. 更新 testcases/README.md 索引；刷新 docs/testcase_health.json
6. commit → push 到你的 fork → gh pr create 到 omgwowai/AIGameDevBench:main
7. 过评审：维护者复跑 audit；未通过就按反馈改，改完 push 同一分支
```

不通过第 4 步的自测就开 PR = 一定会被打回。**先让本地 audit 全绿，再提 PR。**

## 第 0 步：环境

```bash
python -m pip install -e ".[dev]"        # 装 aigdbench CLI + 依赖
# 纯 Python 验证器（py_config / py_tscn_diff / py_gdscript_ast）不需要 Godot。
# 运行时验证器（godot_scene_assert / godot_scenetree / visual_static / interaction_routing）
# 需要一个 godot 可执行文件在 PATH，或用 --godot-binary 指定。
```

## 第 1 步：fork + 分支

PR 提到 **`omgwowai/AIGameDevBench` 的 `main`**。外部贡献者走 fork 流程：

```bash
gh repo fork omgwowai/AIGameDevBench --clone   # 或网页 fork 后 git clone 你的 fork
cd AIGameDevBench
git checkout -b testcase/<简短描述>            # 一个 PR 一个（或一小组）testcase
```

## 第 2 步：生成骨架

```bash
aigdbench scaffold --id my-new-case --category behavior_logic \
  --source-project /path/to/godot/project \
  --task "让 NPC 沿生成的路径移动"
```

它在 `testcases/my-new-case/` 下生成 folder 型骨架（含 `baseline/`、`testcase.toml`、验证器占位）。
**新 case 首选 folder 型**（自包含、任意机器可跑）；git 型不可移植，除非有充分理由不要用。

## 第 3 步：目录格式与 testcase.toml

### 目录布局

```
testcases/<id>/
  testcase.toml        # 必需：manifest
  baseline/            # folder 型：起点 Godot 项目快照（会被拷进临时区并 git init）
  fix.diff             # 推荐：黄金改动（folder 型）；git 型叫 good.diff
  verifier.gd          # godot_scenetree / visual_static / interaction_routing 用
  verifier_scene.tscn  # godot_scene_assert 用
  expected.json        # py_config 用
  expected_delta.json  # py_tscn_diff 用（配 baseline/<scene>.tscn）
  arch_rules.json      # py_gdscript_ast 用
```

### testcase.toml（字段以 `src/aigamedevbench/testcase.py` 的 `load_testcase` 为准）

```toml
[testcase]
id = "my-new-case"
category = "behavior_logic"        # 见下方五类
source_kind = "folder"             # folder（首选）| git
# git 型才需要：baseline_ref = "<源 repo commit SHA>"
source_repo = "authored:my-set"    # 可选，仅记录来源
task = """
用自然语言把要 AI 做的事写清楚，且能被 oracle 客观验证。
写清可观测的成功判据（返回值 / 节点属性 / 数值 / 运行时行为）。
"""

[verifier]
type = "godot_scene_assert"        # 见下方验证器类型
entry = "verifier_scene.tscn"      # 该验证器读取的入口文件

[scoring]
mode = "checkpoints"               # checkpoints | fields | tristate | weighted

[provenance]
author = "你的名字/handle"
created = "2026-07-14"
notes = "golden 改了什么、为什么 noop 应得 0、oracle 只测哪个 delta"
```

### 五个 category ↔ 典型验证器

| category | 含义 | 典型验证器 | 需 Godot |
|---|---|---|---|
| `behavior_logic` | 运行时行为对不对 | `godot_scene_assert` / `godot_scenetree` | 是 |
| `intent_translation` | 自然语言意图 → 正确数值/配置 | `py_config` | 否 |
| `precise_edit` | 精确改动且无副作用 | `py_tscn_diff` | 否 |
| `architecture` | 代码结构/依赖约束 | `py_gdscript_ast` | 否 |
| `visual_audio` | 视觉/布局 | `visual_static` | 是 |

> 套件里 `behavior_logic` 已偏多。**优先补另外四类**（尤其纯 Python 验证器的类别，评审更快、
> 不依赖 Godot）。验证器各自的 expected 文件格式与 `verifier.gd` 写法，见
> [`authoring-testcases/references/verifier_types.md`](../authoring-testcases/references/verifier_types.md)。

## 第 4 步：本地自测（PR 能否合并的硬门槛）

两条硬不变量：**noop → 0，golden → 1**。

```bash
# 一步到位（推荐）：门禁单个 case
python scripts/audit_testcases.py --testcases-dir ./testcases \
  --godot-binary <godot> --only my-new-case

# 或分别看两个原始动作：
aigdbench run --testcases-dir ./testcases --testcase my-new-case --driver noop --godot-binary <godot>
aigdbench run --testcases-dir ./testcases --testcase my-new-case --driver patch \
  --patch testcases/my-new-case/fix.diff --godot-binary <godot>
```

audit **退出码 0 且无 flag** 才算合格。常见 flag 与根因（修法详见姊妹 skill）：

- `leaky_baseline`：noop 拿到分 → oracle 奖励了起点已有状态，改成门控或裁剪 setup 断言。
- `patch_not_one` / `no_fix_diff`：golden 没到满分或缺 fix.diff → 修验证器或补黄金改动。
- `overbroad_oracle`：change-set oracle 太宽（「改任意文件就过」）→ 收紧到 golden 真正触及的文件。
- `nonportable_abs_path`：硬编码绝对路径 → 用 folder 型自包含快照。

> 想「这次先跳过 audit 直接提 PR」？停。维护者会复跑 audit，跳过只会被打回。

## 第 5 步：更新索引与健康快照

```bash
# 把新 case 加进 testcases/README.md 的索引表（id / category / verifier / source_kind / 备注）
# 刷新机器可读健康快照：
python scripts/audit_testcases.py --testcases-dir ./testcases \
  --godot-binary <godot> --json docs/testcase_health.json
```

不要把脚手架/模板留在 `testcases/` 顶层（会被当成 case 收集）；模板放 `testcases/_templates/`。

## 第 6 步：提交 PR 到 AIGameDevBench

```bash
git add testcases/my-new-case testcases/README.md docs/testcase_health.json
git commit -m "test(bench): add <id> (<category>/<verifier>)"
git push -u origin testcase/<简短描述>

gh pr create --repo omgwowai/AIGameDevBench --base main \
  --title "test(bench): add <id> (<category>)" \
  --body "$(cat <<'BODY'
## 这个 testcase
- id / category / verifier / source_kind：
- task 要 AI 做什么：
- golden 改动（fix.diff 改了哪些文件/行）：
- 为什么 noop 应得 0，oracle 只测哪个 delta：

## 自测证据
- `aigdbench audit --only <id>`：noop=0 / golden=1，退出码 0，无 flag
- （可选）bad.diff 得分严格在 (0, golden) 之间
BODY
)"
```

**PR 描述里贴出本地 audit 结果**（noop=0 / golden=1）——这是评审第一眼要看的东西。

## 第 7 步：过评审

- 维护者会在带 Godot 的环境**复跑 `aigdbench audit`**。任何 `leaky_baseline / patch_not_one /
  overbroad_oracle / nonportable_abs_path` 都会要求先修。
- 按反馈改 → 在**同一分支** commit + push，PR 自动更新，无需重开。
- 合并后你的 case 进入 `testcases/`；若被选进精选集会另行再平衡到 `testcases_filtered/`。

## 边界（什么不该提）

- 手感 / 平衡 / 纯视觉微调：没有稳定结构化信号，做不成干净判别器。
- 「重写整个系统」式大范围 task：oracle 无法聚焦，range 太大。
- 直接评测某个 AI harness：那是 `aigdbench run`，不是新增 testcase。

## 参考

- 设计纪律与修法：[`authoring-testcases`](../authoring-testcases/SKILL.md)
  （golden 改动、oracle 收紧、漏分基线、变异播种/历史挖掘/GameDevBench 导入三条配方）。
- 验证器细节：[`authoring-testcases/references/verifier_types.md`](../authoring-testcases/references/verifier_types.md)。
- 数据集格式与逐 case 索引：[`testcases/README.md`](../../../testcases/README.md)。
- 命令速查与准入契约：仓库根 [`README.md`](../../../README.md)。
