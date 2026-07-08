# Parallel Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 AIGameDevBench 双层并行执行：按配置同时跑多个 experiment cell，并在 cell 内同时跑多个 testcase attempt，同时保持 per-attempt workspace / log / Codex home 隔离。

**Architecture:** 新增 `execution_config.py` 读取 `experiments/execution.yaml`。改造 CLI run path：experiment 层用 `ThreadPoolExecutor` 并行 cell，cell 层用 `ThreadPoolExecutor` 并行 testcase，并用一个全局 semaphore 限制总 attempt 数。每个 attempt 新建 driver、独立 workspace、独立 logs/artifacts/result，cell 完成后串行归并 report。

**Tech Stack:** Python 3.10+, stdlib `concurrent.futures` / `threading` / `dataclasses`, existing `click`, `PyYAML`, `pytest`.

## Global Constraints

- Documentation: 写文档使用中文，保留 English keywords。
- 并发度不加 CLI 参数；从 `experiments/execution.yaml` 读取。
- 第一版 checked-in 配置为 `experiment_jobs: 2`、`testcase_jobs: 2`、`max_jobs: 4`。
- 被测试 agent 只拿到 `{workspace}` 和 `{task_file}`；`TASK.md` 位于 `workspace/TASK.md`。
- 强隔离由被测 agent 自己的 sandbox / approval / workspace 参数实现；runner 不声称 OS-level sandbox。
- 每个 attempt 必须独立创建 driver；不得共享 `CommandHarnessDriver` 实例。
- report / final_results / csv 只由 cell owner 归并写入；worker 只写 attempt-local 文件。

---

### Task 1: Execution Config

**Files:**
- Create: `src/aigamedevbench/execution_config.py`
- Create: `experiments/execution.yaml`
- Test: `tests/test_execution_config.py`

**Interfaces:**
- Produces: `ExecutionConfig(experiment_jobs: int, testcase_jobs: int, max_jobs: int)`
- Produces: `load_execution_config(path: str | Path | None = None) -> ExecutionConfig`

- [ ] **Step 1: Write failing tests**

```python
from aigamedevbench.execution_config import ExecutionConfig, load_execution_config


def test_load_execution_config_reads_parallelism(tmp_path):
    path = tmp_path / "execution.yaml"
    path.write_text(
        "parallelism:\n  experiment_jobs: 3\n  testcase_jobs: 4\n  max_jobs: 5\n",
        encoding="utf-8",
    )

    assert load_execution_config(path) == ExecutionConfig(
        experiment_jobs=3,
        testcase_jobs=4,
        max_jobs=5,
    )


def test_load_execution_config_defaults_to_two_two_four_when_missing(tmp_path):
    assert load_execution_config(tmp_path / "missing.yaml") == ExecutionConfig(
        experiment_jobs=2,
        testcase_jobs=2,
        max_jobs=4,
    )


def test_load_execution_config_rejects_invalid_values(tmp_path):
    path = tmp_path / "execution.yaml"
    path.write_text(
        "parallelism:\n  experiment_jobs: 0\n  testcase_jobs: 2\n  max_jobs: 4\n",
        encoding="utf-8",
    )

    try:
        load_execution_config(path)
    except ValueError as e:
        assert "experiment_jobs must be >= 1" in str(e)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_execution_config.py -q`

Expected: FAIL with missing module.

- [ ] **Step 3: Implement module and config file**

Create `execution_config.py` with dataclass defaults, YAML parsing, validation that all three values are >= 1 and `max_jobs >= 1`.

Create `experiments/execution.yaml`:

```yaml
parallelism:
  experiment_jobs: 2
  testcase_jobs: 2
  max_jobs: 4
```

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_execution_config.py -q`

Expected: PASS.

### Task 2: Fixed Workspace Path and Streaming Log

**Files:**
- Modify: `src/aigamedevbench/workspace.py`
- Modify: `src/aigamedevbench/runner.py`
- Modify: `src/aigamedevbench/driver.py`
- Test: `tests/test_runner.py`
- Test: `tests/test_driver.py`

**Interfaces:**
- `run_testcase(..., workspace_name: str | None = None)`
- `folder_workspace(..., workspace_name: str | None = None)`
- `isolated_workspace(..., workspace_name: str | None = None)`
- `CommandHarnessDriver(..., log_name: str | None = None)`

- [ ] **Step 1: Write failing tests**

Add runner test that calls `run_testcase(..., workspace_root=attempt_dir, workspace_name="workspace")` and a fake driver asserts `workspace == attempt_dir / "workspace"` and `TASK.md` is inside that workspace.

Add driver test that starts a process printing a line and sleeping, then polls `logs/harness.log` before process exit and sees the printed line.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_runner.py::test_run_uses_fixed_attempt_workspace tests/test_driver.py::test_command_driver_streams_to_log_before_exit -q`

Expected: FAIL because fixed workspace and realtime log are not implemented.

- [ ] **Step 3: Implement fixed workspace and streaming log**

Update workspace helpers to use `workspace_root / workspace_name` when provided. Remove existing fixed workspace dir before preparing it.

Update command driver so `log_name="harness.log"` writes that filename and appends output lines as they arrive, then appends final status.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_runner.py::test_run_uses_fixed_attempt_workspace tests/test_driver.py::test_command_driver_streams_to_log_before_exit -q`

Expected: PASS.

### Task 3: Attempt Runner

**Files:**
- Modify: `src/aigamedevbench/codex_harness.py`
- Modify: `src/aigamedevbench/cli.py`
- Test: `tests/test_codex_harness.py`
- Test: `tests/test_experiment_cli.py`

**Interfaces:**
- `materialize_attempt_codex_runtime(runtime: CodexHarnessRuntime | None, attempt_dir: Path) -> CodexHarnessRuntime | None`
- CLI helper `_run_one_attempt(...) -> dict`

- [ ] **Step 1: Write failing tests**

Add Codex harness test that materializes a cell runtime, clones an attempt runtime under `attempt/codex_home`, and asserts env `CODEX_HOME` points to the attempt copy, not the cell template.

Add experiment CLI test with command harness that writes `os.getcwd()` and `CODEX_HOME` to workspace. Assert per-testcase record includes `attempt_dir`, `workspace_path`, `log_path`, `events_path`, and Codex attempts have distinct `codex_home`.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_codex_harness.py::test_attempt_codex_runtime_isolated_copy tests/test_experiment_cli.py::test_parallel_attempts_write_isolated_attempt_outputs -q`

Expected: FAIL.

- [ ] **Step 3: Implement attempt helper**

In worker:

- create `cell_dir/attempts/<safe testcase id>/`
- set `workspace_root=attempt_dir`
- set `workspace_name="workspace"`
- set `artifacts_dir=attempt_dir / "artifacts"`
- set command log dir to `attempt_dir / "logs"` with `log_name="harness.log"`
- write `events.jsonl` state transitions
- clone Codex runtime per attempt when available
- create a fresh driver instance for that attempt
- write `attempt/result.json`

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_codex_harness.py::test_attempt_codex_runtime_isolated_copy tests/test_experiment_cli.py::test_parallel_attempts_write_isolated_attempt_outputs -q`

Expected: PASS.

### Task 4: Dual-Level Scheduler

**Files:**
- Modify: `src/aigamedevbench/cli.py`
- Test: `tests/test_experiment_cli.py`

**Interfaces:**
- `_run_experiment(..., execution_config: ExecutionConfig | None = None) -> list[dict]`
- `_run_selected_testcases(..., execution_config: ExecutionConfig | None = None, global_limiter: threading.Semaphore | None = None) -> dict`

- [ ] **Step 1: Write failing tests**

Add a test with two cells and two testcases per cell. Fake harness increments/decrements a JSON counter under lock and records max concurrency. Assert max concurrency <= 4 and at least 2 when using default config. Assert final report order follows testcase order, not finish order.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_experiment_cli.py::test_experiment_uses_execution_config_for_dual_level_parallelism -q`

Expected: FAIL because scheduler is serial.

- [ ] **Step 3: Implement scheduler**

Use `ThreadPoolExecutor(max_workers=execution_config.experiment_jobs)` for cells. Inside each cell use `ThreadPoolExecutor(max_workers=execution_config.testcase_jobs)` for attempts. Wrap each attempt in `global_limiter.acquire()` / `release()`.

Keep unsupported orchestration path serial and explicit.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_experiment_cli.py::test_experiment_uses_execution_config_for_dual_level_parallelism -q`

Expected: PASS.

### Task 5: Regression and Docs

**Files:**
- Modify: `README.md`
- Test: existing targeted tests

- [ ] **Step 1: Document execution config and attempt outputs**

Add README section explaining `experiments/execution.yaml`, attempt directories, and agent sandbox responsibility.

- [ ] **Step 2: Run targeted verification**

Run:

```powershell
uv run pytest tests/test_execution_config.py tests/test_driver.py tests/test_runner.py tests/test_codex_harness.py tests/test_experiment_cli.py -q
```

Expected: PASS.

- [ ] **Step 3: Run smoke experiment**

Run:

```powershell
uv run python -m aigamedevbench.cli run --experiment experiments/filtered30-smoke.yaml --driver patch --results-dir results/parallel-smoke
```

Expected: exit 0, cell reports written, `manifest.json` includes `execution_config`, each testcase has attempt output.

## Self-Review Checklist

- Spec coverage: 双层并行、config-only 并发、global cap、attempt directory、streaming logs、per-attempt Codex home、stable report merge、timeout update retained are all covered.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: `ExecutionConfig`, `workspace_name`, `log_name`, `attempt_dir`, `workspace_path`, `events_path`, `codex_home` are named consistently.
