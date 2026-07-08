# 验证器类型参考

六种验证器。前三种纯 Python（无需 Godot），后三种需 `godot` 在 PATH（或 `--godot-binary`）。
权威实现见 `src/aigamedevbench/verifiers/`，类型白名单见 `testcase.py` 的 `VERIFIER_TYPES`。

`testcase.toml` 里 `[verifier].type` 决定用哪个，`[verifier].entry` 指向它读的入口文件。
`[scoring].mode` ∈ `checkpoints | fields | tristate | weighted`。

---

## 1. py_config（intent_translation）

把自然语言意图翻成正确数值/配置。读 `expected.json`，在 `files_glob` 命中的配置里按
`aliases` 找字段，比对 `expected`（数值用 `tol`）。

`expected.json`：
```json
{
  "fields": [
    {
      "name": "elite_attack_is_base_plus_20pct",
      "aliases": ["attack", "attack_power", "atk"],
      "files_glob": "data/**/*.{json,tres}",
      "expected": 60,
      "tol": 0.001,
      "base": 50,
      "must_differ_from_base": true
    }
  ]
}
```

- `must_differ_from_base: true` 时，值等于 `base` 判失败 → 防「照抄基线」骗分。
- `mode = "fields"`。这是补类别失衡最省力的一类：无需 Godot。

## 2. py_tscn_diff（precise_edit）

精确改场景且无副作用。读 `expected_delta.json` + `baseline/<scene>.tscn`，对比工作区场景的
增删节点 / 改属性。三态打分（`mode = "tristate"`）：缺意图改动→fail，有意图但有副作用→partial，
干净命中→pass。**oracle 必须收紧到 golden 真正动的那几个节点/属性**，否则 partial/pass 失真。

## 3. py_gdscript_ast（architecture）

代码结构 / 依赖约束。读 `arch_rules.json`，对 `target_files` 跑规则：
`forbid_import_glob` / `forbid_hardcoded_res_path` / `require_extends`。每违反一条扣 `weight`，
`score = max(0, 100 - 扣分) / 100`（`mode = "weighted"`）。适合「不许 import X / 必须 extends Y /
不许硬编码 res:// 路径」这类架构约束。

---

## 4. godot_scene_assert / godot_scenetree（behavior_logic）

运行时行为。把 `verifier.gd` 注入工作区跑 headless，脚本 `print` 出
`{"assertions":[{"name","pass","detail","expected","actual"}]}`，据此逐 checkpoint 打分，
跑完删除临时脚本（anti-gaming）。

**为什么走场景模式（`godot_scene_assert`）而非 `--script`**：被测代码常用 autoload 单例全局名
（如 `QuestManager.progress_quest(...)`）。`--script`（`extends SceneTree`）模式下 autoload 不加载，
全局名解析不了。`godot_scene_assert` 用 `godot --headless --path <ws> <verifier_scene.tscn>` 跑场景，
让 autoload 生效。

`verifier_scene.tscn` 用占位符引脚本 + 引被测主场景：
```
[ext_resource type="Script" path="res://__VERIFIER_GD__" id="1"]
[ext_resource type="PackedScene" path="res://scenes/main.tscn" id="2"]
...
script = ExtResource("1")
```
runner 会把 `__VERIFIER_GD__` 换成实际注入的临时脚本路径。

### verifier.gd 的 checkpoint 惯例（关键）

`extends Node`，每个 checkpoint 一条断言，**fail-fast**：第一个失败后，把剩余未到达的
checkpoint 也补成 failed，使分母 = 全部 checkpoint 数，跨 run 可比。骨架（节选自
`testcases/gdb-task_0013/verifier.gd`）：

```gdscript
extends Node

const CHECKPOINTS := ["a", "b", "c"]   # 全部 checkpoint 名，定分母
var checks := []

func _ready() -> void:
    run_validation()

func _record(name, condition, detail, expected = null, actual = null) -> bool:
    checks.append({"name": name, "pass": condition, "detail": detail,
        "expected": expected, "actual": actual})
    return condition

func _emit() -> void:
    var seen := {}
    for c in checks: seen[c.name] = true
    for n in CHECKPOINTS:                      # 补齐未到达的为 failed
        if not seen.has(n):
            checks.append({"name": n, "pass": false,
                "detail": "not reached (an earlier checkpoint failed)"})
    print(JSON.stringify({"assertions": checks}))
    get_tree().quit()

func run_validation() -> void:
    if not _record("a", <断言>, "...", <expected>, <actual>): return _emit()
    if not _record("b", <断言>, "...", <expected>, <actual>): return _emit()
    _emit()
```

**防漏分**：不要写 `scene_loads`、`has_physics_process` 这类任何起点都满足的 setup checkpoint
来直接给分。需要前置检查就让它失败即整体 fail（门控），但满足本身不计分；或只写 task 真正
引入的断言。`mode = "checkpoints"`。

## 5. visual_static（visual_audio）

结构化布局断言（节点位置/锚点/尺寸/可见性等），同样走 `verifier.gd` 注入、print assertions。
适合「UI 面板按模板布局」这类可结构化判定的视觉任务。需要真实显示的（`requires_display`）不收。

## 6. interaction_routing（behavior_logic 子类）

点击路由 / 焦点顺序的运行时断言，机制同上。

---

## survey_bad_case（git 型专用，回归harness）

不是从零写的验证器，而是从 AIGameDevCollecter Survey 导出的真实坏 session 回归壳。读
`survey_bad_case.json`，要求新 harness 输出：①改了相关文件（`must_change_one_of`，**必须收紧**）
②不重现 `bad.diff` ③通过 L0/L1。手写新 case 一般**不用**这个类型，优先用上面六种之一。
