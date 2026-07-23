# Running AIGameDevBench on BeaverHub (`beaver-godot`)

AIGameDevBench 在 BeaverHub 官方 catalog 里以 **`beaver-godot`** 这个 beaver 发布，
绑定唯一 task **`aigdbench`**（digest 钉版，见 beaverhub 仓库 `images/beavers.yaml`，
`source_repo: omgwowai/AIGameDevBench`）。镜像是一个一次性打分器：内置 headless
Godot + aigdbench 引擎，**不消费任何 secret、不出网**。

## 输入 / 输出契约（spec/v1）

- 读 `/in/input.json`：`{"driver": "noop" | "patch", "testcase": "<id>"}`
  - `driver` 必填：`noop` 跑坏基线（契约要求得分 **0.00**）；`patch` 应用内置
    golden 修复（契约要求得分 **1.00**）。
  - `testcase` 可选，缺省 `tetris-clear-scores`。
- 写 `/out/result.json`（`mean_score` 0..1 + 逐 checkpoint 结果），同时把
  人类可读的评分明细打到 stdout（pod 日志即结果传输通道）。
- 非法/缺失输入 → **exit 2（CONFIG_ERROR）**，不触达打分器。

## Operator 快速开始（在已 enroll 的 BeaverServer/操作机上）

```bash
# 1) doctor 必须全绿（Harbor robot 凭据按名解析，勿放进 shell 历史）
beaver doctor

# 2) 物化 profile（见下）后，先离线渲染再真实提交
beaver image run beaver_hub-public/beaver-godot \
  --input smoke-input.json --profile-dir ./profiles --dry-run
beaver image run beaver_hub-public/beaver-godot \
  --input smoke-input.json --profile-dir ./profiles --confirm --wait --collect

# 3) 运行记录落在 $PWD/.beaverhub/runs/<run-id>/（相对当前目录！）
# 4) 清理本次运行的受管资源（Job + 输入 ConfigMap）
beaver run cleanup <run-id> --namespace <ns> --context <ctx> --confirm
```

`smoke-input.json` 就是 `{"driver":"patch","testcase":"tetris-clear-scores"}`；
换成 `{"driver":"noop"}` 应得 0.00 —— 两次跑分构成最小健康检查。

## Profile

模板在 [`beaverhub/profile-beaver-godot.example.yaml`](beaverhub/profile-beaver-godot.example.yaml)。
**这是一个 example，不是部署要求**：里面所有值（context / namespace / node-pool
label / PVC claim / mount path / 镜像）都是 `${PLACEHOLDER}`，没有任何一个是
schema 规定的稳定公共默认值，也没有任何 secret。**稳定版 Python CLI 不做 `${VAR}`
环境变量展开**——先用 `envsubst` 物化成具体 profile 再传 `--profile-dir`（模板头部
有完整命令、含每个占位符的说明）。具体的 namespace / PVC / node-pool 取值由
operator 按自己的集群环境决定并下发，不应该写死在仓库里。

## 已知边界（截至 2026-07）

- **off-server 客户端（npm `@omgwowai/beaverhub` 的 Go CLI）**：`beaver use` 的本地
  doctor 门禁会检查 catalog 里全部 Harbor 空间（含与 beaver-godot 无关的私有
  pr-review 空间），本机没有 robot 凭据时会被拦；而 `beaver run submit --server`
  的通用 dispatch 输入目前只透传 `{repo, ref, task, pr?}`，**带不动
  `driver`/`testcase`** ——所以真实提交目前要在 enrolled 操作机上用
  `beaver image run` 完成。两点都是 beaverhub 侧待修的客户端问题，修复后本节应删。
- 本仓库的 dashboard Run tab 与 `scripts/run_k8s_matrix.sh` 走**直连 kubectl +
  Harbor**，与 BeaverHub 无关，两条路径互不依赖。
