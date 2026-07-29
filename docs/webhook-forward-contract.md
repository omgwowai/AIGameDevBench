# github-webhook receiver → benchmark 转发契约

> 面向 receiver 镜像作者（xiaojun）。目标：让已部署的 `github-webhook` receiver 在收到
> 一次**已校验的 push** 后，转发触发一次 AIGameDevBench 并行 benchmark。
> receiver 源码不在可访问的 git 仓库、镜像在个人 Harbor 项目 `xiaojun_private`，
> 因此这部分改动由你完成；下游 orchestrator + runner 镜像 + benchmark 已由 winter 实现。

## 需要新增的行为

在现有 `POST /github` handler 里，**HMAC 校验通过、且事件属于允许集**（现状 env
`ALLOWED_GITHUB_EVENTS=push,workflow_run,ping`；我们只关心 `push`）之后，
追加一个 **fire-and-forget** 的转发请求，然后照常给 GitHub 回 `202 Accepted`。

关键约束：

1. **不阻塞、不改变给 GitHub 的响应**。转发用后台 / `void fetch(...)`，即使失败也只记日志，
   仍返回 202。GitHub 只关心 receiver 是否 2xx。
2. **只在 `push` 事件转发**（`X-GitHub-Event: push`）。`ping` / `workflow_run` 不转发。
3. **保留现有 HMAC 校验**。转发发生在校验通过之后，不要放行未校验请求。

## 转发请求规格

```
POST  $BENCH_TRIGGER_URL
Headers:
  Content-Type: application/json
  x-bench-token: $BENCH_TRIGGER_TOKEN      # 若该 env 非空则必须带上
Body (JSON):
  {
    "delivery": "<X-GitHub-Delivery 头的值>",   // 必填，去重键
    "repo":     "<payload.repository.full_name>", // 可选，如 "omgwowai/stardive-2d-flo"
    "ref":      "<payload.ref>",                 // 可选，如 "refs/heads/main"
    "after":    "<payload.after>"                // 可选，push 后的 head sha
  }
```

- `delivery` 用 GitHub 的投递 ID（`X-GitHub-Delivery`）。orchestrator 以它去重，
  redelivery 不会重复触发。**务必透传，不要自造。**
- 其余字段可空；orchestrator 只把它们记进 `results/<delivery>/trigger.json` 做溯源。

## 新增两个环境变量

| env | 说明 | 示例 |
|---|---|---|
| `BENCH_TRIGGER_URL` | orchestrator 触发端点。**空则不转发**（等价关闭该功能）。 | `http://10.0.1.135:8899/trigger` |
| `BENCH_TRIGGER_TOKEN` | 共享 token，非空时作为 `x-bench-token` 头发送。防误触。 | 任意随机串 |

两者都缺省为空，因此**这次改动向后兼容**：不配这两个 env 时 receiver 行为与现在完全一致。

部署侧（winter 负责）：镜像发布后按 `../winter-update-runbook.md`
`kubectl -n webhook set image` 更新，并 `kubectl -n webhook set env deployment/github-webhook
BENCH_TRIGGER_URL=... BENCH_TRIGGER_TOKEN=...` 注入。

## orchestrator 端（已实现，供参考）

`scripts/bench-orchestrator.sh --mode http --port 8899 --token <same-token> \
   --image harbor.omgwow.ai/beaver_hub-public/aigdbench-runner:latest`

- `GET  /healthz` → 200 `{"ok":true}`（可用于连通性探活）
- `POST /trigger` → 校验 `x-bench-token` → 校验 `delivery` 非空 → 立即回 202，
  后台起一次 benchmark（每 testcase 一个 k8s Job，claude + agentic-game-development 插件）。

## Node 参考片段（示意，按你的实际框架调整）

```js
// 在 HMAC 校验通过、event === 'push' 之后：
const url = process.env.BENCH_TRIGGER_URL;
if (url) {
  const body = JSON.stringify({
    delivery: req.headers['x-github-delivery'] || '',
    repo: payload.repository?.full_name || '',
    ref: payload.ref || '',
    after: payload.after || '',
  });
  const headers = { 'Content-Type': 'application/json' };
  const tok = process.env.BENCH_TRIGGER_TOKEN;
  if (tok) headers['x-bench-token'] = tok;
  // fire-and-forget: 不 await，失败只 log，不影响下面的 202
  fetch(url, { method: 'POST', headers, body })
    .then(r => log.info({ message: 'forwarded benchmark trigger', status: r.status }))
    .catch(e => log.warn({ message: 'benchmark trigger forward failed', err: String(e) }));
}
// ... 照常 return 202 ...
```

> 注：若 receiver pod 到 orchestrator 主机（`10.0.1.135`）网络不可达，我们有**无需改 receiver**
> 的 fallback：orchestrator `--mode watch-logs` 直接 tail receiver 日志按 delivery 去重触发。
> 但优先用本转发方案（更实时、可带 token、无需读日志权限）。

---

## v2 追加：转发 `pull_request` 事件（gated 候选流程）

> 用于 `docs/spec-v2.md` 的 **PR 触发 gated benchmark**：PR 的 head 作为候选被隔离评测，
> 只有严格优于历史最高分才自动 merge + 发布。**向后兼容**——不转发 PR 事件时，只有旧的
> push 事后流程生效。orchestrator 已能同时处理 push 与 pull_request（按事件分发）。

### receiver 需要做的
1. 允许集加入 `pull_request`（GitHub 仓库 Settings→Webhooks 勾选 “Pull requests”；
   receiver env `ALLOWED_GITHUB_EVENTS` 加 `pull_request`）。
2. HMAC 校验通过后，对 `X-GitHub-Event: pull_request` 也转发（同一 `/trigger` 端点、同一 token）。
3. **只转发 `action ∈ {opened, synchronize, reopened, ready_for_review}`**——其余（closed/labeled…）不转发。
   （orchestrator 侧也会再过滤一次，双保险。）

### PR 转发 body（在 push 字段基础上增加）
```
POST $BENCH_TRIGGER_URL   (Headers 同上: Content-Type + x-bench-token)
Body (JSON):
  {
    "event":     "pull_request",                 // 必填, 区分事件类型
    "action":    "<payload.action>",             // opened|synchronize|reopened|...
    "delivery":  "<X-GitHub-Delivery>",          // 必填
    "repo":      "<payload.repository.full_name>",// 如 "omgwowai/agentic-game-development"
    "pr_number": <payload.pull_request.number>,  // 必填(PR 号, merge 对象)
    "head_sha":  "<payload.pull_request.head.sha>", // 必填(候选 commit, 去重键)
    "base_ref":  "<payload.pull_request.base.ref>"  // 通常 "main"
  }
```
- orchestrator 也接受**嵌套** `"pull_request": {"number":..,"head":{"sha":..},"base":{"ref":..}}`
  形式（原始 GitHub payload 直传）——二者任一即可。
- **去重键是 `head_sha`**（不是 delivery）：`synchronize` 每次 push 到 PR 都重投递，同一 head 只测一次。

### orchestrator 端（已实现）
`POST /trigger` 收到 PR 事件 → 校验 → 立即回 202 → 后台调
`scripts/bench-candidate.sh --pr <n> --commit <head_sha> --repo <o/r> [--auto-release]`：
建候选镜像 `:<head_sha>` → 并行 benchmark → 门禁 `mean_score > best_score` →
达标（且 `--auto-release`）则 `gh pr merge --squash` + 版本 bump + 发布，否则只在 PR 评论分数。

### Node 参考片段（PR 分支，追加到 push 分支旁）
```js
// event === 'pull_request' 且 action ∈ 允许集:
const url = process.env.BENCH_TRIGGER_URL;
if (url && ['opened','synchronize','reopened','ready_for_review'].includes(payload.action)) {
  const pr = payload.pull_request || {};
  const body = JSON.stringify({
    event: 'pull_request', action: payload.action,
    delivery: req.headers['x-github-delivery'] || '',
    repo: payload.repository?.full_name || '',
    pr_number: pr.number, head_sha: pr.head?.sha || '', base_ref: pr.base?.ref || 'main',
  });
  const headers = { 'Content-Type': 'application/json' };
  const tok = process.env.BENCH_TRIGGER_TOKEN;
  if (tok) headers['x-bench-token'] = tok;
  fetch(url, { method:'POST', headers, body }).catch(e => log.warn(String(e)));
}
```

---

## ⚠️ 现行镜像 `1.0.3-amd64` 的实测缺陷（待 xiaojun 修复）

在 mc-winter-zhao 集群 `webhook` ns 抓 `deployment/github-webhook` 日志实测（2026-07-13）：

- `ALLOWED_GITHUB_EVENTS=push,workflow_run,ping,pull_request` ✅ 已含 `pull_request`；
- `BENCH_TRIGGER_URL=http://10.0.1.135:8899/trigger` ✅ 已配；
- `pull_request` / `action=opened` **能被 accept**：
  ```json
  {"message":"accepted GitHub webhook delivery","event":"pull_request",
   "action":"opened","delivery":"10bc0530-...","repository":"omgwowai/agentic-game-development"}
  ```
- **但转发的 body 仍是旧 push 契约的 4 字段**，且 `opened` 时 `after` 为 null：
  ```json
  {"message":"forwarded benchmark trigger","status":202,
   "delivery":"10bc0530-...","repo":"omgwowai/agentic-game-development",
   "ref":null,"after":null}
  ```
  即 **没有 `event` / `action` / `pr_number` / `head_sha` / `base_ref`**。

**后果**：orchestrator 收到后拿不到 `head_sha`（去重键 + 候选 commit），`launch_candidate`
以 `PR event without head_sha; skipping` 直接跳过 → **benchmark 不启动**。这正是当前
“PR webhook 正常触发、却没跑 benchmark” 的根因。

**根因**：现行 1.0.3 的转发逻辑只读 push payload 的 `after`，未按上面的 v2 片段读
`payload.pull_request.head.sha`。请按「Node 参考片段（PR 分支）」补全转发 body 后重打镜像。

### 修复后自检（镜像更新 + 重新触发一个 PR opened 后）

```bash
KC=~/mc-winter-zhao-kubeconfig
# 转发行应出现 event/action/pr_number/head_sha/base_ref，且 head_sha 非空：
kubectl --kubeconfig $KC -n webhook logs deployment/github-webhook --since=10m \
  | grep 'forwarded benchmark trigger' | tail -1
# orchestrator 侧应新增去重键 pr-<num>-<head_sha>，并生成候选目录：
tail -n 3 .orchestrator/seen; ls -dt results/cand-* 2>/dev/null | head -1
```

判定通过：`forwarded` 行带非空 `head_sha`，且 `.orchestrator/seen` 出现 `pr-<num>-<sha>`。
