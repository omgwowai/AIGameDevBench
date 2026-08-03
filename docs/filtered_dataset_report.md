> **2026-07 治理更新（当前实况：36 个，以 `testcases_filtered_manifest.json` 为准）**：
> - **单一事实源**：新增 `scripts/make_manifest.py` 自动生成 manifest（数量/分布/约束检查）。本文及 README 中的历史数字不再手工维护，一律以 manifest 为准。
> - **可解性门禁**：新增 `scripts/audit_solvability.py`（golden 必须纯文本可复现）。`survey-history_2023-06-09_73dc20c_000` 因 good.diff 含二进制资产（缺失字体/PNG）被隔离至 `testcases_quarantine/`。
> - **任务文本分级**：所有 case 增加 `info_level` 字段（`spec` = 给行为规格，考实现；`file-hint` = 只给文件线索+症状，考定位诊断）。survey-history 3 个 case 补充了玩家视角症状描述；`survey-stardive_26f37eb` 任务文本中泄漏的验证器内部断言已改写为可观察行为契约。
> - **类别修正**：3 个 survey-history case 的 golden 实为 .gd 行为修复，category 由 architecture 改为 behavior_logic。
> - **防污染**：新增 `BENCHMARK_CANARY.md`（canary GUID 注入所有 testcase.toml）；正式评测应使用私有镜像中的 golden/verifier。
> - **已知未决**：verifier `godot_scene_assert` 占比 67%，超出 40% 治理上限（见 manifest `constraint_violations`）；消除需要补充 py_*/visual 类新 case。难度校准（参考 harness 通过率）字段已在 manifest 预留，待有 Godot 运行环境后执行。

> **2026-07 更新（当前实况：30 个）**：本报告正文描述的是早期 50 个版本，分布数字已过时。
> 当前 `testcases_filtered/` 为 **30 个**，最近一次改动：
> - **移除** 4 个较"送分"的 `gdb-task_*`（behavior_logic / godot_scene_assert），缓解 behavior_logic 过度集中；
> - **新增** 4 个源自 `gdquest-demos/godot-open-rpg` 真实修复 commit 的 `survey-history_*` case（区分度更高的资源崩溃 / 组合缺陷）：
>   `73dc20c_000`、`9cbd293_003`、`019b51c_009`、`1406822_013`。
>   它们是 folder 型但 baseline 来自**共享项目快照**（`snapshot` 字段；快照不入库，由 `scripts/make_snapshots.py` 按需生成），
>   打分用 `scoring.mode="gated"`（noop=0 / `good.diff`=1），验证器沿用 `survey_bad_case`。
> - **筛选过程**：从 485 个 git case 里，按验证器 oracle 区分度筛出 16 个候选（全 godot-open-rpg，带运行时 L0 oracle），
>   再用容器（含 Godot）逐个实测 golden 是否能干净过 L0/L1 门禁——**只有 4 个能**。其余 12 个撞上项目主场景/其它场景的
>   **既有崩溃**（`resources still in use`、脚本属性错误等，与被测 bug 无关），故淘汰。
> - **配套门禁修复**：L0/L1 默认排除 `addons/`（第三方插件的编辑器专用场景在 headless 下的信号/资源噪声不是被测对象），
>   可经 `config global.validation.excluded_path_prefixes` 覆盖。此修复救活了 3 个原本被 addons 噪声误拦的候选。
> - **当前分布**：category = behavior_logic ×15、architecture ×6、precise_edit ×4、intent_translation ×3、visual_audio ×2；
>   verifier = godot_scene_assert ×17、survey_bad_case ×4、py_config ×3、py_gdscript_ast ×2、py_tscn_diff ×2、visual_static ×2（**6 种全覆盖**）。
> - 迁移脚本：`scripts/_migrate_survey_to_filtered.py`（选中清单在其中）；repo→URL 映射：`scripts/_survey_repos.py`；快照生成：`scripts/make_snapshots.py`。

---

# 精选数据集报告 — `testcases_filtered/`（50 个）

> 目的：让读者快速、准确地认识**精选子集**，并通过**具体 testcase 例子**理解每类任务到底长什么样、怎么打分。
> 统计基准：每个 case 的 `testcase.toml`。日期：2026-07-01。
> （全量数据集见 `full_dataset_report.md`。）

---

## 0. 一句话概览

从全量 519 个里精选 **50 个最有代表性**的 case，用于**快速评测 / 演示 / 开发迭代**。

- **34 个手工精制**（68%）—— 有专门 verifier、明确 checkpoints、golden/bad 参照，质量最高；
- **16 个 survey**（32%）—— 从 485 个自动挖掘 case 里按题材多样性 + 实质性挑出，覆盖 6 个游戏项目、15+ 个子系统。

与全量最大的不同：**判定方式从「93% 单一 verifier」变成 6 种全覆盖**，`visual_audio` 等稀有类型被主动保留。它是人为**再平衡**的子集——牺牲"代表真实频率"换取"覆盖广、质量高、判定多样、可读性强"。

---

## 1. 挑选依据（明确的两步策略）

1. **质量优先**：全部 34 个手工 case 无条件纳入。它们自包含、有专门 verifier 和 checkpoints、`provenance` 里写清了 trap 与评分梯度。
2. **多样性补充**：从 485 个 survey 中挑 16 个补到 50，标准为：
   - **题材多样** —— 按源仓库（游戏类型）加权，覆盖尽可能多的游戏子系统；
   - **实质性** —— 只选真实行为/逻辑/架构缺陷，剔除纯格式/版本号/空白 churn（结果：15 个 high + 1 个 medium substance，0 个 low）。

---

## 2. 分布（每维对比全量）

### 2.1 按 category
| category | 数量 | 占比 | vs 全量 |
|---|---:|---:|---|
| behavior_logic | 36 | 72% | 64.4% → 72%（骨干多为行为逻辑） |
| intent_translation | 6 | 12% | 8.3% → 12%（提升） |
| precise_edit | 4 | 8% | 10.4% → 8%（相近） |
| architecture | 2 | 4% | 16.6% → 4%（手工 arch case 少） |
| visual_audio | 2 | 4% | **0.4% → 4%**（把全量仅有的 2 个都纳入） |

### 2.2 按 verifier（与全量差异最大）
| verifier | 数量 | 占比 | vs 全量 |
|---|---:|---:|---|
| godot_scene_assert | 23 | 46% | **4.4% → 46%** |
| survey_bad_case | 16 | 32% | 93.4% → 32% |
| py_config | 5 | 10% | 1.0% → 10% |
| py_tscn_diff | 2 | 4% | 0.4% → 4% |
| py_gdscript_ast | 2 | 4% | 0.4% → 4% |
| visual_static | 2 | 4% | 0.4% → 4% |

→ **6 种 verifier 全覆盖**，从"单一"变为运行时断言主导、判定方式均衡。

### 2.3 按来源
| 来源 | 数量 | source_kind |
|---|---:|---|
| 手工（folder + gdb-task） | 34 | `folder` |
| survey（git 挖掘） | 16 | `git` |

---

## 3. 代表性例子（读懂"具体内容"看这里）

以下每个例子给出 **真实 task 文本 + verifier + 评分陷阱**，覆盖全部 5 个 category / 6 种 verifier。

### 3.1 behavior_logic × godot_scene_assert — `health-damage-death`
> **task**：实现 `health.gd` 的伤害/死亡逻辑。`take_damage(amount)` 扣血但须 clamp 在 0（不为负）；血量归零时置 `is_dead=true` 并 emit `died` 信号；`died` **必须只触发一次**，死亡后再受击不得重复 emit、不得把血量压到负数。
>
> **verifier**：`godot_scene_assert`（加载场景运行，断言运行时状态）
> **评分梯度（provenance）**：baseline 无 clamp 无信号 → noop=0；bad.diff 会 clamp+emit 但每次致命打击都重复 emit → `died_emitted_exactly_once` 失败 → 3/5=0.6；golden 用 `is_dead` 守卫 → 1.0。

**看点**：这是"分级陷阱"的典范——部分正确（0.6）与完全正确（1.0）由一个边界条件（重复 emit）区分。

### 3.2 precise_edit × py_tscn_diff — `collision-layer-precise-edit`
> **task**：`main.tscn` 里 Player（CharacterBody2D）当前在 collision_layer 1。把它移到 layer 2（`Player.collision_layer = 2`）。**其它一律不动**：不碰 Player 的 collision_mask、不碰 Enemy、不碰任何其它属性。
>
> **verifier**：`py_tscn_diff`（在**节点路径粒度**比对 `.tscn` 结构化差异）
> **评分梯度**：干净的单节点改动 → 1.0；改对了但**扰动了另一个节点**（如 Enemy 的层）→ `no_side_effects` 失败 → 0.5；没改对目标 → 0。

**看点**：考察"外科手术式精确编辑"——不仅要改对，还要**不产生副作用**。

### 3.3 architecture × py_gdscript_ast — `no-hardcoded-res-path`
> **task**：`spawner.gd` 在调用处拼了硬编码字符串 `"res://entities/goblin.tscn"` 传给 `load()`。重构成用 `preload()` 常量（或 `@export`）引用，不再让路径字面量散落在函数体里。必须保持正确生成 goblin，只改"资源引用方式"。
>
> **verifier**：`py_gdscript_ast`（解析 GDScript AST 查结构约束）
> **评分梯度**：规则 `forbid_hardcoded_res_path` —— `res://` 字面量只允许直接位于 `preload()/load()` 内。baseline 把路径存进变量再 `load(var)` → 字面量被标记 → 罚满 → 0；golden 用 `const X := preload("res://...")` → 通过 → 1.0；**只是整理代码但仍保留硬编码字符串 → 仍 0**。

**看点**：架构约束由 AST 规则而非运行结果判定，"看起来更整洁"骗不过它。

### 3.4 intent_translation × py_config — `platformer-jump-retune`
> **task**：平台跳跃调参在 `player_tuning.json`（move_speed / gravity / jump_velocity）。跳跃顶点高度 = `jump_velocity² / (2·gravity)`，滞空时间 = `2·jump_velocity / gravity`。设计要求：顶点高度**翻倍**、滞空时间**不变**。据此更新数值，不改 move_speed。
>
> **verifier**：`py_config`（检查数值字段），scoring `mode = fields`
> **评分梯度**：**耦合常量陷阱**——要在滞空不变下让顶点翻倍，须同时改 jump_velocity（600→1200）**和** gravity（1000→2000）。只改 jump_velocity 会让 gravity 过时 → 1/2 = 0.5；noop=0。

**看点**：考察把自然语言意图 + 物理公式翻译成一组**相互耦合**的数值。

### 3.5 visual_audio × visual_static — `hud-healthbar-anchor`
> **task**：`hud.tscn` 里 HealthBar 用原始左上偏移摆放，窗口缩放时会漂移。把它重锚到**右上角**：anchor_left/right=1.0，anchor_top/bottom=0.0，并调整（负的 left/right）偏移让它仍以原尺寸显示在右上，缩放时钉在右边缘。
>
> **verifier**：`visual_static`（verifier 以 `--script` 模式 extends SceneTree，自己加载场景查布局属性）
> **评分梯度**：baseline 用默认 0 锚点 → noop=0；**陷阱**：AI 只微调原始偏移而不改 anchor → 缩放时仍会坏；golden 锚到右上（anchor_left=anchor_right=1.0）→ 1.0。

**看点**：唯一的视觉类考察——正确答案是"改锚点"，"挪偏移"是似是而非的陷阱。

### 3.6 survey × survey_bad_case（回归重现）— 两个例子
survey case 脱敏后 task 统一为中性形式，只给受影响文件：

> **`survey-fixcommit_2019-01-06_f213d11_027`**（godot-open-rpg，寻路）
> task：`Investigate and fix the defect in the following file(s), making the affected feature behave correctly: godot/local_map/grid/GameBoard.gd.`
> 真实缺陷（挑选时经 bad.diff 确认）：`find_path` 未检查目标格是否为障碍，会把无效目标喂给 A*。verifier 检测是否复发原始坏例。

> **`survey-fixcommit_2022-04-22_a8e1e06_024`**（a-little-game-called-mario，推箱子解谜）
> task：`...behave correctly: scenes/sokoban/SokobanPlayer.gd.`
> 真实缺陷：`try_move` 状态机——输入应仅在成功移动时消费、推箱需校验箱后空格、推动后需刷新渲染。

**看点**：survey case **不明说缺陷是什么**，只指出文件——考察 AI 的**定位+诊断+修复**全链路，更接近真实开发。

### 3.7 gdb-task（教程改编的手工 case）— `gdb-task_0002`
> **task**：让抛射物推进 "Shoot Em Up" 任务——每次与敌人重叠时调用 `QuestManager.progress_quest` 记 kill 步骤，命中后 `queue_free`，并在飞出屏幕 50 像素时自我移除。
> **verifier**：`godot_scene_assert`；`provenance` 标注源自 YouTube 教程（Chevifier - Quest Manager For Godot 4）。

**看点**：来自真实教程场景的任务系统集成，题材贴近实际 Godot 开发教学。

---

## 4. 16 个 survey case 覆盖的子系统

按 6 个源仓库（游戏类型）分布，每个都经 bad.diff 确认为实质性缺陷：

| 源仓库（类型） | 子系统 |
|---|---|
| Super-Mario-Bros（平台） | 战斗/计分、敌人 AI、存档/读档、投射物物理 |
| jdungeon（多人 RPG） | 行为树状态机、投射物碰撞、对话/NPC 交互 |
| tabletop-club（桌游） | 物理交互、网络多人大厅、资源导入 |
| godot-open-rpg（回合 RPG） | 寻路、任务系统 |
| a-little-game-called-mario（平台） | 推箱子解谜、角色碰撞 |
| godot-open-rts（即时战略） | 攻击调度、导航架构 |

---

## 5. 何时用精选集

| 场景 | 是否用 filtered |
|---|---|
| 快速冒烟 / 演示 / 开发迭代 | ✅ 首选（小、快、多样、可读） |
| 考察运行时行为能力 | ✅（46% godot_scene_assert） |
| 验证 verifier 全链路是否都能跑 | ✅（6 种 verifier 各有代表） |
| 正式广度评测 / 统计显著性 | ❌ 用全量 519 |
| 研究真实修复的自然分布 | ❌ filtered 是人为均衡子集，不代表总体频率 |
