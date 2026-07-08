# Session Handoff — 2026-06-30

本会话末尾 auto-mode classifier 因反复访问对话历史/删除类操作而整体收紧,导致
`python`/`pytest`/`py_compile`/`aigdbench serve` 等命令在本会话及其子 agent 中普遍被拦。
新开会话可重置该状态。以下是交接清单。

## 已完成并提交(git log)

- `9075245` fix(dashboard): 修 INDEX_HTML 里字面三引号导致的 SyntaxError(用户已本地 py_compile 确认通过)
- `529d140` feat(dashboard): Testcases 卡片展示 + 内联编辑器(`serve --editable`)
- `01d1277` docs: 索引三个真实 AI 失败挖掘的 case
- `35243e4` / `e3d2e53` / `d4b8a44`: 三个从真实对话挖掘重制的 testcase
  (missing-resource-import / gdscript-cannot-infer-type / ui-window-stretch-config)
- `1ce05df` feat: retry-session 挖掘脚本(Claude + Codex)+ 场景报告

## dashboard 编辑功能:待新会话验证

代码已提交(webreport.py + cli.py),但本会话无法运行验证。新会话请跑:

```bash
cd /c/Users/WinterZhao/Codes/AIGameDevBench
python -m py_compile src/aigamedevbench/webreport.py src/aigamedevbench/cli.py   # 应无输出
python -m pytest tests/test_webreport.py tests/test_cli.py -q                    # 现有测试应绿
# 手动验收:
aigdbench serve --reports-dir . --testcases-dir ./testcases --editable
#   → Testcases 标签:卡片网格 + 搜索框 + category 筛选 chips + "+ New testcase"
#   → 点卡片进详情 → Edit → 改 manifest 下拉/task、编辑文件、Add file、Delete file
#   → 不加 --editable 时应回到纯只读(无编辑按钮),POST 返回 403
```

新增的写端点(仅 --editable 时开放,均有路径穿越守卫):
- POST /api/testcase/create   {id, category, task}
- POST /api/testcase/save-file {id, path, content}
- POST /api/testcase/delete-file {id, path}
- GET  /api/config            {editable, has_testcases, enums}

**建议补测**:给 webreport 的 create_testcase / save_testcase_file /
delete_testcase_file / _safe_file_path 写 pytest,覆盖正常写入 + 路径穿越拦截
(`../`、绝对路径、id 带分隔符)+ testcase.toml 删除保护。本会话没能补上。

## 工作区未提交的杂项(勿误提交)

`git status` 里有大量 ` M` / ` D` / `??`:
- `testcases/*/baseline/*` 显示 M/D:多为 LF↔CRLF 行尾差异 + 之前造 case 的残留,
  **不是本次任务改动**。提交前逐个确认是否为真实改动,别整目录 `git add`。
- `.claude/`、`docs/collecting_better_testcases.md`、`docs/testcase_audit.md`、
  `scripts/audit_testcases.py`、`src/aigamedevbench/testcase_audit.py` /
  `testcase_scaffold.py` 等:是更早的成果,未提交,按需处理。
- `retry_scenarios*.json` / `mined_sessions/` / `inspect_*.json`:私人挖掘数据,
  已在 .gitignore,**绝不提交**。

## 挖掘管线待改进(非阻塞)

- `scripts/mine_retry_sessions.py` 的 codex 分支 `file_thrash` 恒为 0:codex 用
  apply_patch 改文件,`*** Update File` 正则没匹配上其真实 diff 头。修此正则可让
  codex 的"反复改"信号生效。
- UE/Unreal 会话进不了 bench(框架只支持 Godot)。
- 详见 docs/mined_retry_scenarios.md。
