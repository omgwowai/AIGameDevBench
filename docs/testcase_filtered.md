# `testcases_filtered/` 系统性分析

> 对当前精选评测集 `testcases_filtered/` 的结构化梳理：规模、分布、按来源家族的逐例清单、
> 难度与区分度、覆盖缺口与优化方向。数据快照日期：2026-07-16，共 **37 个 testcase**。
> 术语与运行机制见 [`docs/dev.md`](dev.md)。

---

## 0. 一句话概览

37 个 testcase，全部 `source_kind=folder`（本地 baseline 或按需快照）。以 **behavior_logic × godot_scene_assert × checkpoints** 为主干（约六成），来源分四个家族：手工精选 22、gdb-task 4、survey-history 4、survey-stardive 7。核心契约（noop=0 / golden=1）由 `aigdbench audit` 全量守卫。

---

## 1. 分布

### 1.1 按 category（能力维度）
| category | 数量 | 占比 |
|----------|-----:|-----:|
| behavior_logic | 21 | 57% |
| architecture | 7 | 19% |
| precise_edit | 4 | 11% |
| intent_translation | 3 | 8% |
| visual_audio | 2 | 5% |

> **偏科**：behavior_logic 独大；visual_audio / intent_translation 偏薄（见 §5 缺口）。

### 1.2 按 verifier（验证机制）
| verifier_type | 数量 | 机制 |
|---------------|-----:|------|
| godot_scene_assert | 24 | 注入场景 + verifier.gd，headless 断言 checkpoint |
| survey_bad_case | 4 | 回归重现：改相关文件 + 不复现 bad.diff + 过 L0/L1 |
| py_config | 3 | 配置字段匹配 |
| py_tscn_diff | 2 | .tscn 结构差异 |
| py_gdscript_ast | 2 | GDScript AST/架构 |
| visual_static | 2 | 结构层视觉断言 |

### 1.3 按 scoring_mode
| mode | 数量 |
|------|-----:|
| checkpoints | 26 |
| gated | 4 |
| fields | 3 |
| tristate | 2 |
| weighted | 2 |

### 1.4 按来源家族
| family | 数量 | 来源 |
|--------|-----:|------|
| 手工精选（authored） | 22 | `authored:representative-set` + 2 个 showcase/无标注 |
| gdb-task | 4 | GameDevBench 教程改编 |
| survey-history | 4 | 公开 repo `gdquest-demos/godot-open-rpg` 真实修复 commit |
| survey-stardive | 7 | 私有 repo `omgwowai/stardive_kugutsushi` 真实修复 commit |

- `source_kind`：**全部 folder**（0 个 git-type）。
- survey 类（11 个）用 `snapshot` 字段引用 `_snapshots/`，由 `scripts/make_snapshots.py` 按需生成（快照 gitignore）。

---

## 2. 逐例清单（按家族）

### 2.1 手工精选（22）
| id | category | verifier | 任务要点 |
|----|----------|----------|----------|
| ability-cooldown-gate | behavior_logic | godot_scene_assert | 技能冷却门控 |
| collision-layer-precise-edit | precise_edit | py_tscn_diff | 精确改碰撞层 |
| component-layer-boundary | architecture | py_gdscript_ast | 可复用组件的边界/继承 |
| damage-formula-refactor | behavior_logic | godot_scene_assert | 伤害公式（含暴击） |
| event-bus-priority-dispatch | behavior_logic | godot_scene_assert | 优先级事件总线 |
| gdscript-cannot-infer-type | behavior_logic | godot_scene_assert | 修类型推断编译错误 |
| hard-mode-difficulty-preset | intent_translation | py_config | 困难难度预设 |
| health-damage-death | behavior_logic | godot_scene_assert | 伤害/死亡逻辑 |
| hud-healthbar-anchor | visual_audio | visual_static | 血条锚点布局 |
| inventory-equipment-system | behavior_logic | godot_scene_assert | 背包+装备系统 |
| loot-drop-rate-rebalance | intent_translation | py_config | 掉落权重再平衡 |
| menu-panel-layout | visual_audio | visual_static | 菜单面板布局 |
| missing-resource-import | precise_edit | godot_scene_assert | 修缺失资源引用 |
| no-hardcoded-res-path | architecture | py_gdscript_ast | 去硬编码 res 路径 |
| pathfinding-npc-bridge-astar | behavior_logic | godot_scene_assert | A* 桥路径跟随（showcase 来源） |
| pickup-inventory-ui-chain | behavior_logic | godot_scene_assert | 拾取→背包→HUD 链路 |
| platformer-jump-retune | intent_translation | py_config | 平台跳跃手感调参 |
| player-fsm-transition-guard | behavior_logic | godot_scene_assert | 玩家状态机转移守卫 |
| reparent-spawnpoint-precise | precise_edit | py_tscn_diff | 精确 reparent 出生点 |
| timer-signal-wire | precise_edit | godot_scene_assert | Timer signal 接线 |
| wave-combat-score-system | behavior_logic | godot_scene_assert | 波次/战斗/计分多系统 |
| wave-spawner-hidden-bugs | behavior_logic | godot_scene_assert | 波次生成器隐藏 bug |

### 2.2 gdb-task（4，GameDevBench 教程改编）
| id | 任务要点 |
|----|----------|
| gdb-task_0007 | Card Pile 资源 |
| gdb-task_0012 | Bullet 速度/存活 |
| gdb-task_0103 | Enemy 导航系统（NavigationAgent2D + Timer 接线） |
| gdb-task_0281 | 卡牌拖拽状态机（多状态、reparent、阈值） |

### 2.3 survey-history（4，公开 repo godot-open-rpg 真实修复）
全部 `architecture` × `survey_bad_case` × `gated`，来源 `gdquest-demos/godot-open-rpg`。
| id | base commit |
|----|-------------|
| survey-history_2023-06-09_73dc20c_000 | 73dc20c |
| survey-history_2023-08-21_9cbd293_003 | 9cbd293 |
| survey-history_2025-09-19_019b51c_009 | 019b51c |
| survey-history_2026-01-26_1406822_013 | 1406822 |

### 2.4 survey-stardive（7，私有 repo stardive_kugutsushi 真实修复）
从 `omgwowai/stardive_kugutsushi` 的 AI 开发史挖掘的真实 bug-fix，改编成 `godot_scene_assert` checkpoint。均带 `bad_case_type=B1`。
| id | category | bug 本质 |
|----|----------|----------|
| survey-stardive_84d8c23_stale-combat-target | behavior_logic | 陈旧 opponent 泄漏 → 打错目标 |
| survey-stardive_26f37eb_retaliation-offender | behavior_logic | 强制反击瞄最近者而非冒犯者 |
| survey-stardive_60282af_guard-reaction-scope | architecture | 守卫反击进全局池 → 全场群殴 |
| survey-stardive_39f9033_rethink-sideeffect-gating | behavior_logic | 无效 LLM 响应仍改好感/目标/记忆 |
| survey-stardive_afda272_rescue-state-gating | behavior_logic | 救助未绑定晕倒状态/不广播 |
| survey-stardive_ee84ed2_stage-shout-broadcast | behavior_logic | 舞台喊话不广播、旁观者不响应 |
| survey-stardive_84039a5_alms-item-transfer | behavior_logic | 施舍物品跨 LLM 往返不转移 |

---

## 3. 难度与区分度

以最近一次全量 benchmark（30 用例基线版，mean **0.946**，claude harness）为参照：

| 分数 | case | 说明 |
|-----:|------|------|
| 0.00 | survey-history_2025-09-19_019b51c_009 | 唯一 fail：harness 改错文件（改了 TASK.md/dialogic/project.godot 而非 combat/*）；且耗时逼近超时 |
| 0.47 | gdb-task_0281 | 卡牌拖拽状态机：checkpoint 顺序 gating，卡在第 10 个（reparent 到 overlay）后全部 not-reached |
| 0.89 | gdb-task_0103 | 导航系统：26/28，卡在 `enemy_target_export` |
| 1.00 | 其余 27 | 满分 |

**观察**：
- **天花板效应明显**：27/30 满分，区分度主要靠 3 个 case 撑。survey-stardive 7 个新用例尚未进入这次 30 用例基线（迁移后需重跑 37 用例版才有数据）。
- **checkpoint 顺序 gating** 会低估真实完成度（0281/0103 都是"做对很多但卡在中段"），放大方差。
- 那个 0 分 case 暴露的是**任务定位歧义**（harness 没找到该改的 combat 目录），部分属任务设计而非能力。

> 注意：survey-stardive 的 7 个用例已单独 audit 验证 **noop=0.00 / golden=1.00**（见迁移记录），是当前区分度最强的新增来源——真实 repo、可观测状态差异。

---

## 4. survey 类覆盖的子系统

11 个 survey case（history 4 + stardive 7）覆盖真实项目的这些子系统：

- **godot-open-rpg**（history）：field/combat 场景加载、资源/字体导入、autoload 依赖、gamepiece/pathfinder 类解析。
- **stardive_kugutsushi**（stardive，"捏 NPC 过日子"的 tavern sim）：战斗目标解析、事件反应作用域、LLM 决策副作用门控、救助/晕倒状态机、舞台表演广播、乞讨物品转移、rethink 优先级。

这些是**动态行为/状态**类 bug（能编译但运行行为错），补齐了手工集偏"静态正确性"的短板。

---

## 5. 覆盖缺口与优化方向

1. **分类失衡**：behavior_logic 21 vs visual_audio 2 / intent_translation 3。建议补 visual_audio、intent_translation。
2. **天花板效应**：需要更多中间态区分力的 case（像 0281），并给每个 case 标注难度分层（easy/med/hard），保证 hard 占比。
3. **checkpoint 顺序 gating**：审视哪些 checkpoint 是真依赖、哪些只是书写顺序；无因果依赖的应改成独立可得分。
4. **来源多样性**：手工 22 占比偏高；继续从真实 repo（survey 流程）扩充，降低自造分布偏移。
5. **评测方法学**：目前多为单次运行（无置信区间）；对含 LLM 随机性的 harness 应 repeat≥3 取均值+CI。
6. **git-type 缺失**：全部 folder-type；真实 git checkout 类可作为另一种保真度来源。

---

## 6. 健康与运行

- **健康校验**：`aigdbench audit --testcases-dir testcases_filtered --godot-binary godot`（本地无 godot 时经 runner 镜像跑，见 dev.md §7）。要求每个 case noop=0 / golden(`fix.diff` 或 `good.diff`)=1。
- **快照生成**（survey 类首次运行前）：`python3 scripts/make_snapshots.py --testcases-dir testcases_filtered`。
- **单例调试**：`aigdbench run --testcases-dir testcases_filtered --testcase <id> --driver noop|patch|command`。

---

## 附：一句话任务索引（全 37）

```
ability-cooldown-gate         技能冷却门控
collision-layer-precise-edit  精确改碰撞层
component-layer-boundary      可复用组件边界/继承
damage-formula-refactor       伤害公式(含暴击)
event-bus-priority-dispatch   优先级事件总线
gdb-task_0007                 Card Pile 资源
gdb-task_0012                 Bullet 速度/存活
gdb-task_0103                 Enemy 导航系统接线
gdb-task_0281                 卡牌拖拽状态机
gdscript-cannot-infer-type    修类型推断编译错误
hard-mode-difficulty-preset   困难难度预设
health-damage-death           伤害/死亡逻辑
hud-healthbar-anchor          血条锚点布局
inventory-equipment-system    背包+装备系统
loot-drop-rate-rebalance      掉落权重再平衡
menu-panel-layout             菜单面板布局
missing-resource-import       修缺失资源引用
no-hardcoded-res-path         去硬编码 res 路径
pathfinding-npc-bridge-astar  A* 桥路径跟随
pickup-inventory-ui-chain     拾取→背包→HUD 链路
platformer-jump-retune        平台跳跃手感调参
player-fsm-transition-guard   玩家状态机转移守卫
reparent-spawnpoint-precise   精确 reparent 出生点
timer-signal-wire             Timer signal 接线
wave-combat-score-system      波次/战斗/计分多系统
wave-spawner-hidden-bugs      波次生成器隐藏 bug
survey-history_*_000/003/009/013  godot-open-rpg 真实修复(架构类)
survey-stardive_84d8c23       陈旧战斗目标泄漏
survey-stardive_26f37eb       反击瞄错目标
survey-stardive_60282af       守卫反击全局池泄漏
survey-stardive_39f9033       LLM 副作用门控
survey-stardive_afda272       救助-晕倒状态绑定
survey-stardive_ee84ed2       舞台喊话广播
survey-stardive_84039a5       施舍物品转移
```
