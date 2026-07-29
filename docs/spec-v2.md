# Spec v2 — PR 触发的 gated benchmark → 自动合并 + 发布

> 取代 v1（push-to-main 事后验证）。核心变化：**benchmark 成为 PR 的合并门禁**——
> 插件的改动以 PR 形式提交，只有当"该 PR 的候选版本在 AIGameDevBench 上的成绩**严格优于
> main 历史最高分**"时，才自动 merge PR 进 main 并发布新 release。不达标的 PR 不进 main。

## 触发：PR 事件（不再是 push）

`agentic-game-development` 仓开/更新 PR → GitHub `pull_request` webhook → receiver → 编排器。

关注的 `action`：`opened` / `synchronize`（PR 有新 push）/ `reopened`。
取用的 payload 字段：

| 字段 | 用途 |
|---|---|
| `pull_request.number` | PR 号；达标后 `gh pr merge` 的对象 |
| `pull_request.head.sha` | **候选 commit**——喂给 `bench-candidate.sh --commit` |
| `pull_request.head.ref` | PR 源分支（= 天然的候选隔离分支，无需再造 bench-<sha>） |
| `pull_request.base.ref` | 目标分支（通常 main）；benchmark 的对比基准 |
| `delivery` | 去重用；但**真正去重键是 head.sha**（synchronize 会重复投递） |

## 端到端流程

```
PR opened/synchronize (head.sha, number, base=main)
  │
  ├─0. 去重         同 head.sha 已测过 → 跳过 (PR 每次 push 都触发, 按 sha 去重)
  │
  ├─1. 候选内容     PR 分支 = origin/main + PR commits (GitHub 已算好 head.sha)
  │                bench-candidate.sh 直接 checkout PR head.sha (无需 cherry-pick;
  │                若 PR 落后 main 太多, 可选 rebase onto main 再测——见"并发"节)
  │
  ├─2. 临时版本     <main版本>-bench.<sha8>, 写入 plugin.json + _bench_source 标记
  │
  ├─3. 候选镜像     vendor PR head 的插件 → build → push image:<sha8> (不可变 tag)
  │
  ├─4. 并行benchmark 每 testcase 一个 k8s Job (claude + 候选插件) → 聚合 report.json
  │                report 记 trigger(PR号/head.sha/作者/subject) + plugin_change + mean_score
  │
  ├─5. 门禁         mean_score > baseline.best_score (main 历史最高) ?
  │      ├ 否 → PR 不合并; 回填 PR 一条 comment(分数/对比/未达标); 保留 report; 结束
  │      └ 是 ↓
  │
  ├─6. 自动合并     gh pr merge <number> --squash --admin  (候选进 main)
  │
  ├─7. 基于 main 重打包  去掉 -bench 后缀 → 正式 semver bump; 刷新 baseline.best_score
  │
  └─8. 发布         push main → release-on-bump.yml 发 release
                    release notes 贴: 来源(PR#/commit/作者/subject) + 测试数据(mean/逐case/
                    delta vs 旧best/镜像 digest)
```

实现：`scripts/bench-candidate.sh`（步骤 1-8），由 `bench-orchestrator.sh` 在收到 PR 事件时以
`--commit <head.sha> --pr <number> --repo <o/r> --auto-release` 调用。

## 与 v1（当前）流程的差异

| 维度 | v1（push 触发） | v2（PR 触发 gated） |
|---|---|---|
| 触发事件 | push 到 main | `pull_request` opened/synchronize |
| commit 何时进 main | **已在 main**，事后测 | **PR 里待定**，达标才 merge |
| 隔离 | 无（已在 main） | PR 分支天然隔离；候选镜像用不可变 `:<sha>` tag |
| 门禁 | `delta >= 0`（不低于当前 release） | `> best_score`（**严格优于历史最高**） |
| 达标动作 | bump + push main（commit 已在） | **`gh pr merge` + bump + 发布** |
| 不达标 | 无门禁，已既成事实 | **PR 不合并**，只评论分数 |
| 历史最高分 | 不记录（只存当前分） | baseline 新增 `best_score`/`best_version` |
| 发布日志 | 通用模板 + baseline 资产 | 正文贴 PR 来源 + 测试数据 |

**本质**：v1 是"进了 main 再看好不好"；v2 是"证明更好才准进 main"。main 变成 benchmark 守护的受保护分支。

## Receiver 侧改造（不在本仓，在 mc-winter-zhao 集群的 github-webhook）

当前 receiver 只转发 push 的 `after`。需扩展为也转发 `pull_request` 事件：
- 订阅 `pull_request` webhook（GitHub 仓库 Settings → Webhooks 勾选 Pull requests）；
- 转发 body 增加：`event:"pull_request"`, `action`, `pr_number`, `head_sha`, `base_ref`, `head_ref`；
- 契约更新见 `docs/webhook-forward-contract.md`（v2 追加字段）。

编排器兼容两种事件：push → v1 路径（保留）；pull_request → v2 候选路径。

## 门禁数据：baseline 增加 best_score

`agentic-game-development/workflow/benchmark-baseline.json` 追加：
```json
{ "version": "<current release>", "mean_score": <该版本分>,
  "best_score": <历史最高>, "best_version": "<达到最高的版本>",
  "plugin_commit": "...", "testcases": {...} }
```
- `mean_score` = 当前 release 被 cut 时的分（v1 语义，保留）；
- `best_score` = **历史最高分**（v2 门禁比较对象）；`bench-candidate.sh` 只在候选**严格超过** best 时更新它。
- 兼容:文件无 `best_score` 时回退用 `mean_score`。

## 效率优化（按收益排序）

1. **插件运行时注入，base 镜像只建一次**（最大收益）：现状每候选都 full build + push ~GB
   含 Godot 的镜像，是最贵一步。改为 base 镜像（Godot+claude+testcases）固定，插件用
   init-container git-sync / ConfigMap / emptyDir 在 Pod 启动注入 `/opt/agd-plugin`。
   每候选从"几分钟 build+push"→"秒级"。彻底消除 stale-cache / 反复推拉的麻烦。
2. **不可变 `:<plugin-tree-hash>` tag + 内容去重**：若坚持 bake，用插件内容 hash 作 tag，
   相同 skills 内容命中已有镜像跳过 build；根治 mutable `:latest` stale 问题。
3. **收集改"完成顺序"而非"启动顺序"**：v1 亲历 matrix 卡在 wait 一个卡死 case（claude
   空转 35min）导致已完成 case 迟迟没收。用 informer/轮询收任意先完成的 Job，消除 head-of-line 阻塞。
4. **卡死 harness 提前中止**：输出停顿检测（N 分钟无 stdout）提前杀，省 deadline 空等（v1 那个 case 白等 35min）。
5. **门禁分级早退**：严格 `>` 下多数候选会被拒。先跑与 PR 改动相关的少量 case 快速否决，明显退步的候选早退，不必全跑 30 + 不必 build 大镜像。
6. **候选流水线 + 串行 merge 点**：候选 A 跑分时 B 可排队 build；merge 串行化，main 移动后对落后候选重测（见并发）。

## 并发 / 竞态

- **多 PR 同时在测**：各自候选镜像 `:<sha>` 隔离，互不干扰。
- **merge 竞态**：候选基于测时的 origin/main；若期间别的 PR 先 merge，main 前进 → best_score 可能已变。
  处理：merge 前**重读 best_score 复核**仍严格优于；若已被超越则不合并、评论"main 已前进，请 rebase 重测"。
- **synchronize 风暴**：PR 频繁 push → 按 head.sha 去重，同 sha 只测一次；旧 sha 的在跑候选可选择取消。

## 关键默认值

| 项 | 默认 |
|---|---|
| 触发 | PR opened/synchronize/reopened |
| 去重键 | head.sha |
| 门禁 | mean_score **>** best_score（严格） |
| 达标动作 | `gh pr merge --squash` + bump + release（需 --auto-release） |
| 候选镜像 tag | `:<head.sha8>`（不可变） |
| 镜像仓 | `beaver_hub-public/aigdbench-runner` |
| 版本 | 临时 `-bench.<sha>`；正式 semver bump（默认 patch） |
