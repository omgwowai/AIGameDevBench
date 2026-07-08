# 从当前 repo 造 testcase 的三条配方

每条都以「先有 golden 改动、再有起点、最后审计」为骨架。命令里的 `TCDIR` 用 testcases 的
**原生路径**（Windows `C:\...`，不要用 git-bash 的 `/c/...`），`GODOT` 是 godot 二进制路径。

---

## 配方 A：变异 / 回归播种（首选，天然干净）

思路：从 repo 历史里挑一个**已工作**的功能 commit，把它**回退**成起点，原 commit 即 golden。
因为 golden 改动 = 被回退的那几行，noop→0 与 golden→1 几乎天然成立，oracle 范围也精确。

```bash
# 1. 在源游戏 repo 里挑一个聚焦的功能 commit
cd <game_repo>
git log --oneline -- <相关路径>        # 找一个「加了某功能」的 commit <FEAT>

# 2. 该 commit 的父提交即起点；FEAT 相对父提交的 diff 即 golden 改动
git diff <FEAT>^ <FEAT> > /tmp/golden.diff      # 正确改动
# baseline = <FEAT>^ 时的项目状态

# 3a. 做成 folder 型（首选，可移植）：
#     把 <FEAT>^ 的项目快照进 baseline/，把 golden.diff 存成 fix.diff
git checkout <FEAT>^
mkdir -p $TCDIR/<id>/baseline && cp -r <game_repo>/* $TCDIR/<id>/baseline/   # 剔除 .git/.godot
cp /tmp/golden.diff $TCDIR/<id>/fix.diff
git checkout -        # 还原 repo

# 3b. 或做成 git 型：testcase.toml 写 source_kind="git"、baseline_ref=<FEAT>^、source_repo=<game_repo>
```

要点：
- 选**只改少数文件**的 commit，oracle 才好收紧。一个 commit 同时改十处不适合。
- baseline 里**剔除** `.git/`、`.godot/`（导入缓存由 runner 重建）、以及任何泄漏答案的文件。
- 验证器照 `references/verifier_types.md` 写：把 golden 引入的行为写成 checkpoint，**不要**给
  起点已有的脚手架记分。

## 配方 B：从 git 历史挖真实失败（survey 风格）

思路：repo 里某次改动**崩过**（L0/L1 报错、反复返工、人多次介入），把那段还原成回归 case。

```bash
cd <game_repo>
git log --oneline                      # 找「崩了」的 commit <BAD> 和「修好」的 <FIX>
git show <BAD>                         # 看崩在哪（如 Parse Error: Could not find type "NPC"）
git diff <BAD>^ <FIX> > /tmp/good.diff  # 从起点到修复版的正确改动
git diff <BAD>^ <BAD>  > /tmp/bad.diff  # 起点到坏版本（用作 bad.diff，证明能排序）
```

- 起点取 `<BAD>^`。golden = `good.diff`，并放 `bad.diff` 作中间分对照。
- 优先做成 **folder 型**快照，避免本套件 survey-* 那种「硬编码绝对路径、换机即废」的坑。
- 若必须 git 型：`source_repo` + `baseline_ref` 写清，并接受审计会标 `nonportable_abs_path`。
- oracle（验证器或 `must_change_one_of`）**只覆盖 good.diff 真正动的文件**，不要把整段 commit
  范围全列进去（套件里有过 94 / 2711 文件的反面教材）。

## 配方 C：导入 GameDevBench 任务（自包含 folder）

GameDevBench 每个 task 是自包含项目 + 黄金验证器（`scenes/test.tscn` + `scripts/test.gd`，打印
`VALIDATION_PASSED/FAILED`）。导入脚本转成本仓 folder 型 case：

```bash
python scripts/import_gamedevbench.py --gdb ../GameDevBench --out testcases --tasks task_0002 task_0003
```

产物布局：
```
gdb-task_0002/
  testcase.toml          # source_kind="folder", verifier.type="godot_scene_assert"
  baseline/              # 起点项目（已剔除 test.* / .godot / 泄答案文件）
  verifier_scene.tscn    # test.tscn 改来，脚本路径换成占位符 __VERIFIER_GD__
  verifier.gd            # test.gd 转译：extends Node，逐 checkpoint 打印 {"assertions":[...]}
  fix.diff               # baseline → ground-truth 的 diff，自测用
```

注意：
- 脚本会跳过 `requires_display` 的 task（首批只接 headless 可验证的纯代码/gameplay 逻辑）。
- `test.gd → verifier.gd` 默认产**单 checkpoint 兜底**；要逐断言部分得分得**手工拆分**成多
  checkpoint（范本见 `testcases/gdb-task_0013/verifier.gd`）。
- **导入后极易出现漏分**：GameDevBench 的起点可能本身是「部分错误实现」，baseline 已满足某些
  checkpoint，导致 noop 非 0。必须把那些非区分性行为从 baseline 移除并重生 `fix.diff`
  （gdb-task_0002 即按此修正）。

---

## 所有配方收尾：审计 + 并入

```bash
# 单 case 审计（folder 型任意目录；git 型先 cd 进 source_repo）
python scripts/audit_testcases.py --testcases-dir "$TCDIR" --godot-binary "$GODOT" --only <id>
```

退出码 0、无 flag 才算合格。常见 flag → 修法：
- `leaky_baseline(noop=...)` → 验证器在给起点已有状态记分；门控或裁剪那些 setup 断言。
- `patch_not_one(...)` / golden 不满分 → 验证器与 task 不符；对齐验证器。
- `no_fix_diff` → folder 型缺 `fix.diff`；补上 golden 改动。
- `overbroad_oracle(N)` → `must_change_one_of` 太宽；收紧到 golden 实际触及的文件。
- `nonportable_abs_path` → git 型固有；尽量改成 folder 型，否则在文档里记清依赖。

合格后更新 `testcases/README.md` 索引表，并重生健康快照
`docs/testcase_health.json`（见 SKILL.md 第 7 步）。
