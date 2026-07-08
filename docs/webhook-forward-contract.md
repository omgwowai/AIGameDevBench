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
   --image harbor.omgwow.ai/xiaojun_private/aigdbench-runner:latest`

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
