---
name: authoring-testcases
description: >-
  当需要给 AIGameDevBench 新增 testcase 时使用：从当前 repo（一个 Godot 游戏 repo 或本基准 repo）
  的内容出发，把一个真实改动变成一个干净可打分的 testcase。触发面包括「给这个功能/这次提交/这个 bug
  造一个 benchmark case」「从这个游戏 repo 里提取 testcase」「把这个 GameDevBench 任务导入进来」
  「扩充 testcase 套件」。核心纪律：先定义 golden 改动与 oracle，再保证 noop→0 / golden→1、
  baseline 不漏分、oracle 收紧，最后用 scripts/audit_testcases.py 审计通过才算数。
  不用于手感/平衡/纯视觉调整这类没有结构化信号的任务，也不用于直接评测某个 harness（那是 aigdbench run）。
metadata:
  expose: [codex, claude]
---

# authoring-testcases

这个 skill 用来从**当前 repo 的内容**新增一个 AIGameDevBench testcase。

一个 testcase = (一个起点状态) + (一段自然语言 task) + (一个冻结的黄金验证器)。它只有在是
**干净判别器**时才算合格：**什么都不做 → 0 分，正确改动 → 满分**，中间分随质量单调。
绝大多数失败 testcase 不是「太难」，而是**测量坏了**。本 skill 的全部纪律都服务于这一条性质。

## 核心纪律（先证据，后实现）

1. **先想清 golden 改动**：在动手造 case 前，明确「正确做法改了哪几个文件、哪几行」。
   这就是 `fix.diff`（folder 型）或 `good.diff`（git 型）。
2. **oracle 只测这次 task 要的 delta**，不测起点已有的脚手架，也不测「改了任意文件」。
3. **造完立刻审计**，未通过不得并入套件：

   ```bash
   python scripts/audit_testcases.py --testcases-dir ./testcases \
     --godot-binary <godot> --only <新 case id>
   ```

   退出码 0 且无 flag 才算合格。任何 `leaky_baseline / patch_not_one / no_fix_diff /
   overbroad_oracle / nonportable_abs_path` 都必须先修掉。

> 想「这次先跳过审计」？停。这就是把坏测量放进套件的那一步。

## 整体流程

```
1. 从 repo 选一个有意义、可观测的改动           → 选 task
2. 定起点（baseline）与 golden 改动             → 选 source_kind
3. 选验证器类型并写 oracle                      → 见 references/verifier_types.md
4. 写 testcase.toml                             → manifest
5. 自测：noop→0、golden→1                       → scripts/audit_testcases.py
6. （推荐）放一个 bad.diff，确认中间分 < golden  → 证明能排序质量
7. 审计全绿 → 更新 testcases/README.md 索引       → 并入
```

## 第 1 步：从当前 repo 选一个 task

好 task 的判据：

- **可观测**：结果能被代码/运行时断言出来（信号、节点属性、数值、运行时行为）。
  手感、平衡、纯视觉调整没有稳定信号，不适合。
- **判别性强**：起点状态下这件事**没做**，正确改动后**做了**。差距越清晰越好。
- **范围聚焦**：一个 task 对应少数几个文件的 delta，不要「重写整个系统」。

从 repo 里找 task 的三条来源（性价比从高到低，详见
[`references/recipes.md`](references/recipes.md)）：

1. **变异/回归播种**：取一个 repo 里**已工作**的功能 commit，把关键改动**回退**成 baseline，
   原 commit 即 golden。天然保证 noop→0、golden→1，oracle 正好是被回退的那几行。
2. **从 git 历史挖真实失败**：repo 里某次 AI/人改崩过（L0/L1 报错、反复返工），把那段还原成 case。
3. **导入 GameDevBench 任务**：`scripts/import_gamedevbench.py`，得到自包含 folder 型 case。

## 第 2 步：定起点形态（source_kind）

| source_kind | 起点 | 何时用 | 可移植性 |
|---|---|---|---|
| `folder`（**首选**） | case 自带 `baseline/` 子目录（一个自包含 Godot 项目） | 任意目录可跑，无外部依赖 | 高 |
| `git` | 源 repo 的某个 commit，runner 用 `git worktree` checkout | 必须在该 repo 内跑 | 低 |

**默认选 `folder`**：把起点项目快照进 `baseline/`，case 就能在任何机器跑。只有当 case 强依赖
该 repo 的大量历史/上下文、快照不现实时才用 `git`，且必须在 `testcase.toml` 里记清
`source_repo` + `baseline_ref`，并接受它不可移植（审计会标 `nonportable_abs_path`）。

> 避免本套件已踩过的坑：survey-* 全是 git 型、硬编码绝对路径，换台机器全部跑不了。

## 第 3 步：选验证器并写 oracle

按 task 的 category 选验证器（六种类型、各自的 expected 文件格式、`verifier.gd` 的
checkpoint 写法，全部见 [`references/verifier_types.md`](references/verifier_types.md)）：

| category | 典型验证器 | 需 Godot |
|---|---|---|
| `behavior_logic` | `godot_scene_assert` / `godot_scenetree` | 是 |
| `intent_translation` | `py_config` | 否 |
| `precise_edit` | `py_tscn_diff` | 否 |
| `architecture` | `py_gdscript_ast` | 否 |
| `visual_audio` | `visual_static` | 是 |

> 套件目前 10/16 是 `behavior_logic`，类别失衡。新增时**优先补**其它四类。

### 写 oracle 的两条铁律

- **不奖励起点已有的状态**。这是「漏分基线」的根因：若验证器里有 `scene_loads`、
  `has_physics_process` 这类**任何起点都满足**的 setup 断言，noop 就会拿到分。
  做法：要么把 setup 断言设为**门控**（不满足就整体 0 分，但满足本身不给分），
  要么干脆只写 task 真正引入的那些断言。
- **change-set oracle 要紧**。`py_tscn_diff` / survey 的 `must_change_one_of` 必须 ⊆
  golden 改动实际触及的文件。「改任意一个文件就过」是无效 oracle（套件里有过 94、甚至
  2711 个文件的反面教材）。

## 第 4 步：写 testcase.toml

```toml
[testcase]
id = "gdb-task_XXXX"            # 或描述性 id
category = "behavior_logic"     # 五类之一
source_kind = "folder"          # folder（首选）或 git
# git 型才需要：baseline_ref = "<commit SHA>"；source_repo = "<repo 路径>"
source_repo = "GameDevBench:task_XXXX"   # 仅记录用
task = """
用自然语言把要 AI 做的事写清楚，可被 oracle 验证。
"""

[verifier]
type = "godot_scene_assert"     # 见 references/verifier_types.md
entry = "verifier_scene.tscn"   # 该验证器读的入口文件

[scoring]
mode = "checkpoints"            # checkpoints | fields | tristate | weighted

[provenance]
author = "..."
created = "2026-..."
notes = "golden 改动是什么、为什么 noop 该 0、oracle 范围"
```

字段语义以 `src/aigamedevbench/testcase.py` 的 `load_testcase` 为准（category /
verifier type / source_kind 都会被校验，写错直接报错）。

## 第 5 步：自测两条硬不变量

```bash
TCDIR='<.../testcases 的原生路径>'   # Windows 用 C:\... 不要用 /c/...
GODOT='<godot 二进制路径>'

# folder 型：任意目录可跑
aigdbench run --testcases-dir "$TCDIR" --testcase <id> --driver noop --godot-binary "$GODOT"
aigdbench run --testcases-dir "$TCDIR" --testcase <id> --driver patch \
  --patch testcases/<id>/fix.diff --godot-binary "$GODOT"
# git 型：必须 cd 进 source_repo 再跑

# 一步到位的审计（推荐）：
python scripts/audit_testcases.py --testcases-dir "$TCDIR" --godot-binary "$GODOT" --only <id>
```

- noop ≠ 0 → baseline 漏分，回第 3 步修 oracle（门控/裁剪 setup 断言）。
- golden ≠ 1 → 验证器写错或与 task 不符，修验证器。
- L0/L1 gate fail → 起点/改动后场景加载不了或有断引用，先修起点。

> 若起点 task 本身是「部分错误实现」，baseline 可能已满足某些 checkpoint。两条不变量是硬的：
> 必须把那些非区分性行为从 baseline 移除并相应重生 golden 改动（参见 gdb-task_0002 的修法）。

## 第 6 步（推荐）：放一个 bad.diff 证明能排序

放一个「部分对/已知有 bug」的改动，确认它得分 **严格在 0 和 golden 之间、且低于 golden**。
这证明验证器在给质量排序，而不仅是 pass/fail。

## 第 7 步：并入套件

- 审计全绿后，把新 case 加进 [`testcases/README.md`](../../../testcases/README.md) 的索引表。
- 重新生成健康快照：`python scripts/audit_testcases.py --testcases-dir ./testcases
  --godot-binary <godot> --json docs/testcase_health.json`。
- 别把脚手架/模板放进 `testcases/` 顶层（会被发现成 case）；模板放 `testcases/_templates/`。

## 参考

- [`references/verifier_types.md`](references/verifier_types.md) — 六种验证器、expected 文件格式、
  checkpoint `verifier.gd` 写法与 fail-fast 惯例。
- [`references/recipes.md`](references/recipes.md) — 变异播种 / 历史挖掘 / GameDevBench 导入三条
  具体配方，含命令与目录布局。
- 方法论与准入契约：[`docs/collecting_better_testcases.md`](../../../docs/collecting_better_testcases.md)。
- 现状健康审计：[`docs/testcase_audit.md`](../../../docs/testcase_audit.md)。
