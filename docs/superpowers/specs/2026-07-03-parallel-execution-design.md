# Parallel Execution Design

## 背景

当前 `aigdbench run --experiment` 先串行跑 cell，再在每个 cell 内串行跑 testcase。`run_testcase()` 已经为每个 testcase 创建隔离 workspace，但 CLI 层共享一个 driver 实例、共享 cell-level `CODEX_HOME`，并且 stdout 直接打到终端。这个结构不适合同时跑多个 cell 和多个 testcase。

## 目标

第一版实现双层并行：

- experiment-level parallelism：同时跑多个 cell。
- cell-level parallelism：同一个 cell 内同时跑多个 testcase。
- global max jobs：所有 cell 和 testcase 的总 attempt 数不能超过全局上限。

并发度不暴露为 CLI 参数，统一从 `experiments/execution.yaml` 读取。第一版配置固定为：

```yaml
parallelism:
  experiment_jobs: 2
  testcase_jobs: 2
  max_jobs: 4
```

每个 testcase attempt 都必须有独立目录。被测试 agent 只通过自己的 CLI 限制参数约束读写面，runner 只把 `TASK.md` 和 `workspace/` 暴露给 agent。

## 非目标

- 不做 OS-level sandbox。强隔离由 Codex、Claude Code 等被测 agent 自己的 sandbox / approval / workspace 参数实现。
- 不新增 `--experiment-jobs`、`--testcase-jobs`、`--max-jobs` 之类 CLI 参数。
- 不改变 verifier、L0/L1、score schema 的核心语义。
- 不实现 unsupported orchestration 的运行时。非 `single-agent` 继续显式 `unsupported`。

## Attempt 目录

每个 testcase attempt 写入：

```text
results/<experiment_id>/<cell_id>/attempts/<testcase_id>/
  workspace/
    TASK.md
    ...baseline files...
  logs/
    harness.log
    events.jsonl
  artifacts/
    changes.diff
    files/
  result.json
  codex_home/              # only when agent_cli=codex
```

`workspace/` 是被测试 agent 的 `cwd`，也是唯一可写工作目录。`TASK.md` 放在 `workspace/` 内，`{task_file}` 指向这个文件。runner 不把 testcase source dir、results root、repo root、logs dir、artifacts dir 作为 command placeholder 暴露给 agent。

## 调度模型

`ExperimentScheduler` 负责加载 `ExecutionConfig` 并持有一个全局 semaphore。`CellScheduler` 在每个 cell 内创建 testcase worker pool。每个 testcase worker 在启动 attempt 前 acquire 全局 semaphore，结束后 release。

```text
run --experiment
  -> load experiment YAML
  -> load experiments/execution.yaml
  -> ThreadPoolExecutor(max_workers=experiment_jobs)
      -> run one cell
          -> materialize cell harness template
          -> write manifest
          -> ThreadPoolExecutor(max_workers=testcase_jobs)
              -> run one testcase attempt under global semaphore
          -> merge attempt result.json in testcase order
          -> write report.json / final_results.json / final_results.csv
```

cell-level report 只能由主线程归并写入。worker 只写自己的 `attempt/result.json` 和日志文件，避免并发写同一个 report。

## Driver 隔离

每个 attempt 都新建 driver 实例：

- `CommandHarnessDriver` 不共享，因为它有 `label`、`last_outcome`、`_counter` 可变状态。
- patch driver 在 worker 内读取当前 testcase 的 `fix.diff`。
- noop driver 在 worker 内创建。

command driver 的 stdout/stderr 实时追加到 `attempt/logs/harness.log`，同时写 `attempt/logs/events.jsonl`。外层守护 agent 可以 tail 这些文件判断进度，不依赖主进程 stdout。

## Codex Harness 隔离

cell 开始时仍 materialize 一个 cell-level Codex template home，用于 manifest probe。每个 attempt 再从该 template 复制出：

```text
results/<experiment_id>/<cell_id>/attempts/<testcase_id>/codex_home/
```

worker env 只传自己的 `CODEX_HOME`。这样同一个 cell 内多个 Codex testcase 不共享 session、cache、history 或其他可变状态。

## 输出与可复现性

report 内 testcase 顺序必须保持输入顺序，不按完成顺序写。每条 record 增加：

- `attempt_dir`
- `workspace_path`
- `events_path`
- `log_path`
- `configured_timeout`（如来自 testset）
- `codex_home`（仅 Codex attempt）

`manifest.json` 增加 `execution_config`，记录本次使用的并发配置。

## 错误处理

- 单个 attempt 异常只生成该 testcase 的 error record，不中断 cell。
- 单个 cell 异常只生成该 cell 的 error report，不中断其他 cell。
- timeout 自更新逻辑保留：所有 report 归并完成后，继续调用现有 testset timeout update。
- `--no-stream` 只影响主终端输出；attempt 的 `harness.log` 始终流式写入文件。

## 设计风险

`cwd=workspace` 和最小 env 不是强安全沙箱。被测 agent 如果被错误配置成允许任意 filesystem access，仍可能读到外部文件。因此 experiment 的 harness command / harness config 必须显式使用 agent 自己的限制参数，例如 Codex 的 `--cd {workspace}` 和对应 sandbox 配置，Claude Code 的 workspace/permission 参数。runner 负责最小暴露面，但不声称 OS-level containment。
