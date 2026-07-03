# GameDevBench Factorial Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first implementation slice for factorial GameDevBench runs: experiment YAML, `agent_cli` aliasing, resolved custom harness metadata, orchestration source metadata, per-cell result directories, and manifests.

**Architecture:** Add a focused `experiment_config.py` module for pure parsing, normalization, harness resolution, and fingerprinting. Keep Godot execution in `benchmark_runner.py`, but extend `GodotBenchmarkRunner` with resolved cell metadata and manifest writing. Preserve legacy `--agent` behavior while adding `--agent-cli`, `--harness`, `--orchestration`, and `run --experiment`.

**Tech Stack:** Python 3.10+, stdlib `dataclasses`/`hashlib`/`json`/`argparse`, existing `yaml`, existing pytest suite.

---

## File Structure

- Create `gamedevbench/src/experiment_config.py`
  - Owns harness component keys, normalization, fingerprinting, `OrchestrationSpec`, `ExperimentCell`, `ExperimentSpec`, experiment YAML parsing, and single-cell construction.
- Modify `gamedevbench/src/benchmark_runner.py`
  - Adds `agent_cli` compatibility, metadata fields, manifest writing, per-cell result directory handling, CSV columns, and experiment execution.
- Create `tests/test_experiment_config.py`
  - Unit tests for harness resolution, fingerprint stability, add/remove behavior, and experiment YAML parsing.
- Create `tests/test_benchmark_runner_metadata.py`
  - Unit tests for runner initialization, metadata shape, CSV serialization, manifest writing, and unsupported orchestration behavior.
- Create `tests/test_cli_experiment.py`
  - CLI-level tests for `--agent`/`--agent-cli` compatibility and experiment dispatch without invoking Godot.
- Create `experiments/gd-harness-ab-smoke.yaml`
  - Small example experiment with two cells and `test_task.yaml`.
- Modify `README.md`
  - Add concise usage for factorial variables, experiment YAML, and legacy compatibility.

Do not stage or rewrite the existing unrelated dirty files unless a task explicitly touches them:

- `gamedevbench/src/utils/constants.py`
- `docs/tokenrouter-models.md`
- `tests/test_constants.py`

## Verification Plan

Phase A acceptance assertions for this implementation:

- Legacy CLI still accepts `--agent codex ... run --task-list test_task.yaml`.
- New single-cell CLI accepts `--agent-cli`, `--harness`, and `--orchestration`.
- Experiment YAML expands at least two cells into independent result directories.
- Result JSON, CSV rows, and `manifest.json` include `agent_cli`, orchestration metadata, harness preset/components/fingerprint, task set, experiment id, and cell id.
- Changing one harness component changes `harness_fingerprint`.
- Non-`single-agent` orchestration returns an explicit unsupported result before solver execution.

Final verification commands:

```powershell
uv run pytest tests/test_experiment_config.py tests/test_benchmark_runner_metadata.py tests/test_cli_experiment.py -q
uv run python gamedevbench/src/benchmark_runner.py --help
uv run python gamedevbench/src/benchmark_runner.py --agent codex --model gpt-5.4 --harness gd-baseline --orchestration single-agent run --task-list test_task.yaml --dry-run
uv run python gamedevbench/src/benchmark_runner.py run --experiment experiments/gd-harness-ab-smoke.yaml --dry-run
```

The `--dry-run` flag is added in Task 4 so these commands do not invoke Godot or agent CLIs.

---

### Task 1: Pure Experiment Config Model

**Files:**
- Create: `gamedevbench/src/experiment_config.py`
- Create: `tests/test_experiment_config.py`

- [ ] **Step 1: Write failing tests for harness resolution**

Create `tests/test_experiment_config.py` with:

```python
import textwrap

import pytest

from gamedevbench.src.experiment_config import (
    COMPONENT_KEYS,
    OrchestrationSpec,
    build_single_cell,
    fingerprint_components,
    load_experiment_file,
    normalize_components,
    resolve_harness,
)


def test_normalize_components_keeps_known_keys_and_sorts_unique_values():
    components = normalize_components(
        {
            "skills": ["godot-debugging", "game-dev-verification", "godot-debugging"],
            "mcp_servers": ["godot-screenshot"],
        }
    )

    assert list(components.keys()) == COMPONENT_KEYS
    assert components["skills"] == ["game-dev-verification", "godot-debugging"]
    assert components["mcp_servers"] == ["godot-screenshot"]
    assert components["hooks"] == []


def test_normalize_components_rejects_unknown_component_key():
    with pytest.raises(ValueError, match="Unknown harness component keys: plugins"):
        normalize_components({"plugins": ["not-a-supported-axis"]})


def test_resolve_harness_applies_preset_add_and_remove():
    presets = {
        "gd-full": {
            "components": {
                "instruction_files": ["gd-agents-md"],
                "skills": ["godot-debugging"],
                "hooks": ["session-start-context"],
            }
        }
    }

    resolved = resolve_harness(
        {"preset": "gd-full", "add": {"skills": ["visual-qa"]}, "remove": {"hooks": ["session-start-context"]}},
        presets,
    )

    assert resolved.preset == "gd-full"
    assert resolved.components["instruction_files"] == ["gd-agents-md"]
    assert resolved.components["skills"] == ["godot-debugging", "visual-qa"]
    assert resolved.components["hooks"] == []
    assert resolved.fingerprint.startswith("sha256:")


def test_fingerprint_changes_when_component_changes():
    base = normalize_components({"skills": ["godot-debugging"]})
    changed = normalize_components({"skills": ["godot-debugging", "visual-qa"]})

    assert fingerprint_components(base) != fingerprint_components(changed)


def test_load_experiment_file_expands_cells(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: gd-harness-ab
            task_sets:
              smoke:
                task_list: test_task.yaml
            harness_presets:
              gd-baseline:
                components:
                  skills: []
              gd-skill:
                components:
                  skills: [godot-debugging]
            cells:
              - id: baseline
                model: gpt-5.4
                agent_cli: codex
                orchestration:
                  id: single-agent
                  source_type: builtin
                task_set: smoke
                harness:
                  preset: gd-baseline
              - id: skill
                model: gpt-5.4
                agent_cli: codex
                orchestration:
                  id: single-agent
                  source_type: builtin
                task_set: smoke
                harness:
                  preset: gd-skill
            """
        )
    )

    experiment = load_experiment_file(experiment_file)

    assert experiment.experiment_id == "gd-harness-ab"
    assert [cell.cell_id for cell in experiment.cells] == ["baseline", "skill"]
    assert experiment.cells[0].task_list_file == "test_task.yaml"
    assert experiment.cells[1].harness.components["skills"] == ["godot-debugging"]


def test_build_single_cell_uses_defaults():
    cell = build_single_cell(
        agent_cli="codex",
        model="gpt-5.4",
        harness_name="gd-baseline",
        orchestration_id="single-agent",
        task_list_file="test_task.yaml",
    )

    assert cell.experiment_id == "ad-hoc"
    assert cell.agent_cli == "codex"
    assert cell.orchestration == OrchestrationSpec(id="single-agent", source_type="builtin")
    assert cell.task_set == "test_task"
    assert cell.harness.preset == "gd-baseline"
    assert cell.harness.components == normalize_components({})
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
uv run pytest tests/test_experiment_config.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'gamedevbench.src.experiment_config'`.

- [ ] **Step 3: Implement `experiment_config.py`**

Create `gamedevbench/src/experiment_config.py`:

```python
#!/usr/bin/env python3

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


COMPONENT_KEYS = [
    "instruction_files",
    "skills",
    "mcp_servers",
    "hooks",
    "cli_config",
    "tool_wrappers",
    "context_artifacts",
]

DEFAULT_HARNESS_PRESETS: Dict[str, Dict[str, Any]] = {
    "gd-baseline": {"components": {}},
}


@dataclass(frozen=True)
class ResolvedHarness:
    preset: Optional[str]
    components: Dict[str, List[str]]
    fingerprint: str


@dataclass(frozen=True)
class OrchestrationSpec:
    id: str
    source_type: str = "builtin"
    generator_agent_cli: Optional[str] = None
    generator_model: Optional[str] = None
    workflow_prompt: Optional[str] = None
    workflow_file: Optional[str] = None

    @property
    def is_supported(self) -> bool:
        return self.id == "single-agent" and self.source_type == "builtin"

    @classmethod
    def from_value(cls, value: Any) -> "OrchestrationSpec":
        if value is None:
            return cls(id="single-agent", source_type="builtin")
        if isinstance(value, str):
            source_type = "builtin" if value == "single-agent" else value
            return cls(id=value, source_type=source_type)
        if isinstance(value, dict):
            orchestration_id = str(value.get("id", "single-agent"))
            source_type = str(value.get("source_type", "builtin"))
            return cls(
                id=orchestration_id,
                source_type=source_type,
                generator_agent_cli=value.get("generator_agent_cli"),
                generator_model=value.get("generator_model"),
                workflow_prompt=value.get("workflow_prompt"),
                workflow_file=value.get("workflow_file"),
            )
        raise ValueError(f"Invalid orchestration spec: {value!r}")

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "source_type": self.source_type,
        }
        optional_fields = {
            "generator_agent_cli": self.generator_agent_cli,
            "generator_model": self.generator_model,
            "workflow_prompt": self.workflow_prompt,
            "workflow_file": self.workflow_file,
        }
        data.update({key: value for key, value in optional_fields.items() if value})
        return data


@dataclass(frozen=True)
class ExperimentCell:
    experiment_id: str
    cell_id: str
    model: str
    agent_cli: str
    orchestration: OrchestrationSpec
    task_set: str
    task_list_file: Optional[str]
    harness: ResolvedHarness


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    description: str
    cells: List[ExperimentCell]


def sanitize_identifier(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    sanitized = sanitized.strip("._-")
    return sanitized or "default"


def normalize_components(components: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    components = components or {}
    unknown_keys = sorted(set(components.keys()) - set(COMPONENT_KEYS))
    if unknown_keys:
        raise ValueError(f"Unknown harness component keys: {', '.join(unknown_keys)}")

    normalized: Dict[str, List[str]] = {}
    for key in COMPONENT_KEYS:
        raw_values = components.get(key, [])
        if raw_values is None:
            raw_values = []
        if not isinstance(raw_values, list):
            raise ValueError(f"Harness component '{key}' must be a list")
        normalized[key] = sorted({str(value) for value in raw_values})
    return normalized


def fingerprint_components(components: Dict[str, List[str]]) -> str:
    normalized = normalize_components(components)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _merge_component_lists(
    base: Dict[str, List[str]],
    additions: Optional[Dict[str, Any]] = None,
    removals: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[str]]:
    merged = {key: list(values) for key, values in normalize_components(base).items()}

    add_components = normalize_components(additions)
    for key, values in add_components.items():
        merged[key] = sorted(set(merged[key]) | set(values))

    remove_components = normalize_components(removals)
    for key, values in remove_components.items():
        merged[key] = sorted(set(merged[key]) - set(values))

    return normalize_components(merged)


def resolve_harness(
    harness_spec: Optional[Dict[str, Any]],
    harness_presets: Optional[Dict[str, Any]] = None,
) -> ResolvedHarness:
    harness_spec = harness_spec or {}
    presets = {**DEFAULT_HARNESS_PRESETS, **(harness_presets or {})}
    preset_name = harness_spec.get("preset")

    base_components: Dict[str, Any] = {}
    if preset_name:
        if preset_name not in presets:
            raise ValueError(f"Unknown harness preset: {preset_name}")
        preset_payload = presets[preset_name] or {}
        base_components = preset_payload.get("components", {})

    explicit_components = harness_spec.get("components")
    if explicit_components is not None:
        base_components = explicit_components

    components = _merge_component_lists(
        normalize_components(base_components),
        additions=harness_spec.get("add"),
        removals=harness_spec.get("remove"),
    )
    return ResolvedHarness(
        preset=preset_name,
        components=components,
        fingerprint=fingerprint_components(components),
    )


def _task_set_to_default_name(task_list_file: Optional[str]) -> str:
    if task_list_file:
        return Path(task_list_file).stem
    return "all"


def build_single_cell(
    *,
    agent_cli: str,
    model: str,
    harness_name: str = "gd-baseline",
    orchestration_id: str = "single-agent",
    task_list_file: Optional[str] = None,
    experiment_id: str = "ad-hoc",
    cell_id: Optional[str] = None,
) -> ExperimentCell:
    orchestration = OrchestrationSpec.from_value(orchestration_id)
    harness = resolve_harness({"preset": harness_name})
    task_set = _task_set_to_default_name(task_list_file)
    resolved_cell_id = cell_id or sanitize_identifier(
        f"{model}_{agent_cli}_{harness_name}_{orchestration.id}_{task_set}"
    )
    return ExperimentCell(
        experiment_id=sanitize_identifier(experiment_id),
        cell_id=resolved_cell_id,
        model=model,
        agent_cli=agent_cli,
        orchestration=orchestration,
        task_set=task_set,
        task_list_file=task_list_file,
        harness=harness,
    )


def load_experiment_file(path: str | Path) -> ExperimentSpec:
    experiment_path = Path(path)
    data = yaml.safe_load(experiment_path.read_text()) or {}
    experiment_id = sanitize_identifier(data.get("id", experiment_path.stem))
    description = str(data.get("description", ""))
    task_sets = data.get("task_sets", {}) or {}
    harness_presets = data.get("harness_presets", {}) or {}
    cells: List[ExperimentCell] = []

    for raw_cell in data.get("cells", []) or []:
        task_set_name = str(raw_cell.get("task_set", "all"))
        task_set_payload = task_sets.get(task_set_name, {}) or {}
        task_list_file = raw_cell.get("task_list") or task_set_payload.get("task_list")
        orchestration = OrchestrationSpec.from_value(raw_cell.get("orchestration"))
        harness = resolve_harness(raw_cell.get("harness"), harness_presets)
        cell_id = sanitize_identifier(raw_cell["id"])
        cells.append(
            ExperimentCell(
                experiment_id=experiment_id,
                cell_id=cell_id,
                model=str(raw_cell.get("model", "claude")),
                agent_cli=str(raw_cell["agent_cli"]),
                orchestration=orchestration,
                task_set=task_set_name,
                task_list_file=task_list_file,
                harness=harness,
            )
        )

    return ExperimentSpec(experiment_id=experiment_id, description=description, cells=cells)
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
uv run pytest tests/test_experiment_config.py -q
```

Expected: PASS, `7 passed`.

- [ ] **Step 5: Commit Task 1**

Run:

```powershell
git add gamedevbench/src/experiment_config.py tests/test_experiment_config.py
git commit -m "feat(benchmark): add experiment config model"
```

---

### Task 2: Runner Metadata, Manifest, and Unsupported Orchestration

**Files:**
- Modify: `gamedevbench/src/benchmark_runner.py`
- Create: `tests/test_benchmark_runner_metadata.py`

- [ ] **Step 1: Write failing metadata and manifest tests**

Create `tests/test_benchmark_runner_metadata.py` with:

```python
import csv
import json
from pathlib import Path

from gamedevbench.src.benchmark_runner import GodotBenchmarkRunner
from gamedevbench.src.experiment_config import OrchestrationSpec, resolve_harness


def make_runner(tmp_path):
    harness = resolve_harness({"components": {"skills": ["godot-debugging"]}})
    runner = GodotBenchmarkRunner(
        use_gt=False,
        agent_cli="codex",
        model="gpt-5.4",
        experiment_id="gd-harness-ab",
        cell_id="cell-a",
        task_set="smoke",
        orchestration=OrchestrationSpec(id="single-agent", source_type="builtin"),
        harness=harness,
    )
    runner.results_dir = tmp_path / "results"
    runner.test_results_dir = tmp_path / "test_results"
    runner.progress_file = runner.results_dir / "progress_codex_gpt-5.4.json"
    return runner


def test_runner_metadata_contains_factorial_fields(tmp_path):
    runner = make_runner(tmp_path)

    metadata = runner._factorial_metadata()

    assert metadata["experiment_id"] == "gd-harness-ab"
    assert metadata["cell_id"] == "cell-a"
    assert metadata["agent"] == "codex"
    assert metadata["agent_cli"] == "codex"
    assert metadata["orchestration_id"] == "single-agent"
    assert metadata["orchestration_source_type"] == "builtin"
    assert metadata["task_set"] == "smoke"
    assert metadata["harness_components"]["skills"] == ["godot-debugging"]
    assert metadata["harness_fingerprint"].startswith("sha256:")


def test_final_results_summary_includes_factorial_configuration(tmp_path):
    runner = make_runner(tmp_path)

    summary = runner._create_final_results_summary(1, 0, 0, 0, 1, [{"success": True}])

    assert summary["configuration"]["agent_cli"] == "codex"
    assert summary["configuration"]["orchestration"]["id"] == "single-agent"
    assert summary["configuration"]["task_set"] == "smoke"
    assert summary["configuration"]["harness_components"]["skills"] == ["godot-debugging"]


def test_csv_writes_factorial_columns(tmp_path):
    runner = make_runner(tmp_path)
    csv_path = tmp_path / "results.csv"
    result = {
        "task_name": "task_0002",
        "success": True,
        "message": "ok",
        **runner._factorial_metadata(),
    }

    runner._save_results_to_csv([result], csv_path)

    rows = list(csv.DictReader(csv_path.open()))
    assert rows[0]["agent_cli"] == "codex"
    assert rows[0]["orchestration_id"] == "single-agent"
    assert rows[0]["harness_skills"] == '["godot-debugging"]'


def test_manifest_file_records_cell_reproduction_data(tmp_path):
    runner = make_runner(tmp_path)

    manifest_path = runner._write_manifest(["task_0002"], "test_task.yaml")

    manifest = json.loads(manifest_path.read_text())
    assert manifest["experiment_id"] == "gd-harness-ab"
    assert manifest["cell_id"] == "cell-a"
    assert manifest["tasks"] == ["task_0002"]
    assert manifest["task_list_file"] == "test_task.yaml"
    assert manifest["harness_components"]["skills"] == ["godot-debugging"]


def test_unsupported_orchestration_returns_explicit_result(tmp_path):
    harness = resolve_harness({"components": {}})
    runner = GodotBenchmarkRunner(
        use_gt=False,
        agent_cli="codex",
        model="gpt-5.4",
        orchestration=OrchestrationSpec(id="scripted-parallel-verify", source_type="scripted_workflow"),
        harness=harness,
    )

    result = runner.run_benchmark("task_0002")

    assert result["success"] is False
    assert result["solver_success"] is False
    assert result["orchestration_id"] == "scripted-parallel-verify"
    assert "Unsupported orchestration" in result["message"]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
uv run pytest tests/test_benchmark_runner_metadata.py -q
```

Expected: FAIL because `GodotBenchmarkRunner.__init__` does not accept `agent_cli`, `_factorial_metadata` does not exist, and unsupported orchestration is not handled.

- [ ] **Step 3: Extend runner constructor and metadata helpers**

In `gamedevbench/src/benchmark_runner.py`, add imports:

```python
from importlib.metadata import PackageNotFoundError, version

from gamedevbench.src.experiment_config import (
    COMPONENT_KEYS,
    OrchestrationSpec,
    ResolvedHarness,
    resolve_harness,
    sanitize_identifier,
)
```

Update `GodotBenchmarkRunner.__init__` signature by adding these keyword parameters after `agent` and before `model`:

```python
        agent_cli: Optional[str] = None,
        experiment_id: Optional[str] = None,
        cell_id: Optional[str] = None,
        task_set: Optional[str] = None,
        orchestration: Optional[OrchestrationSpec] = None,
        harness: Optional[ResolvedHarness] = None,
```

Replace the existing `self.agent = agent` block with:

```python
        self.agent_cli = agent_cli or agent
        self.agent = self.agent_cli
        self.experiment_id = sanitize_identifier(experiment_id) if experiment_id else None
        self.cell_id = sanitize_identifier(cell_id) if cell_id else None
        self.task_set = task_set
        self.orchestration = orchestration or OrchestrationSpec(id="single-agent", source_type="builtin")
        self.harness = harness or resolve_harness({"preset": "gd-baseline"})
```

Replace result directory construction with:

```python
        self.run_name = run_name.strip() if run_name else None
        self.safe_run_name = (
            self._sanitize_run_name(self.run_name) if self.run_name else None
        )
        if self.experiment_id and self.cell_id:
            self.results_dir = RESULTS_FOLDER / self.experiment_id / self.cell_id
            self.test_results_dir = self.tasks_dir / "test_result" / self.experiment_id / self.cell_id
        else:
            self.results_dir = (
                RESULTS_FOLDER / self.safe_run_name
                if self.safe_run_name
                else RESULTS_FOLDER
            )
            self.test_results_dir = (
                self.tasks_dir / "test_result" / self.safe_run_name
                if self.safe_run_name
                else self.tasks_dir / "test_result"
            )
```

Replace progress file line with:

```python
        safe_model = model.replace("/", "_") if model else "default"
        safe_agent = self.agent_cli or "validation"
        self.progress_file = self.results_dir / f"progress_{safe_agent}_{safe_model}.json"
```

Add methods inside `GodotBenchmarkRunner` after `_sanitize_run_name`:

```python
    def _factorial_metadata(self) -> Dict:
        """Return experiment metadata copied into summaries, task results, and CSV rows."""
        return {
            "experiment_id": self.experiment_id,
            "cell_id": self.cell_id,
            "agent": self.agent,
            "agent_cli": self.agent_cli,
            "orchestration_id": self.orchestration.id,
            "orchestration_source_type": self.orchestration.source_type,
            "orchestration": self.orchestration.to_dict(),
            "task_set": self.task_set,
            "harness_preset": self.harness.preset,
            "harness_components": self.harness.components,
            "harness_fingerprint": self.harness.fingerprint,
        }

    def _runner_version(self) -> str:
        try:
            return version("gamedevbench")
        except PackageNotFoundError:
            return "0.1.0"

    def _git_commit(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    def _write_manifest(self, tasks: List[str], task_list_file: Optional[str]) -> Path:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            **self._factorial_metadata(),
            "model": self.model,
            "task_list_file": task_list_file,
            "tasks": tasks,
            "git_commit": self._git_commit(),
            "runner_version": self._runner_version(),
            "started_at": datetime.now().isoformat(),
        }
        manifest_path = self.results_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        return manifest_path

    def _unsupported_orchestration_result(self, task_name: str) -> Dict:
        metadata = self._factorial_metadata()
        message = (
            f"Unsupported orchestration: {self.orchestration.id} "
            f"({self.orchestration.source_type})"
        )
        return {
            "task_name": task_name,
            "success": False,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "model": self.model,
            "use_mcp": self.use_mcp,
            "use_runtime_video": self.use_runtime_video,
            "skip_display": self.skip_display,
            "debug": self.debug,
            "solver_success": False,
            "solver_message": message,
            "solver_duration": 0.0,
            "is_rate_limited": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "sandbox_dir": "",
            "result_dir": "",
            **metadata,
        }
```

Also import `PROJECT_ROOT` from constants:

```python
    PROJECT_ROOT,
```

- [ ] **Step 4: Add metadata into result paths**

At the start of `run_benchmark`, before selecting agent/validation path, add:

```python
        if not self.orchestration.is_supported:
            return self._unsupported_orchestration_result(task_name)
```

In every task result dict returned by `_run_benchmark_with_agent`, `run_benchmark` error branches, and skip branches, add:

```python
                **self._factorial_metadata(),
```

In `_create_final_results_summary`, extend `"configuration"` with:

```python
                "agent_cli": self.agent_cli,
                "experiment_id": self.experiment_id,
                "cell_id": self.cell_id,
                "orchestration": self.orchestration.to_dict(),
                "task_set": self.task_set,
                "harness_preset": self.harness.preset,
                "harness_components": self.harness.components,
                "harness_fingerprint": self.harness.fingerprint,
```

In `run_all_tasks`, after tasks are resolved and before resume handling, write manifest:

```python
        self._write_manifest(tasks, task_list_file)
```

- [ ] **Step 5: Extend CSV columns**

In `_save_results_to_csv`, add fieldnames after `"model"`:

```python
            "experiment_id",
            "cell_id",
            "agent_cli",
            "orchestration_id",
            "orchestration_source_type",
            "task_set",
            "harness_preset",
            "harness_fingerprint",
            "harness_instruction_files",
            "harness_skills",
            "harness_mcp_servers",
            "harness_hooks",
            "harness_cli_config",
            "harness_tool_wrappers",
            "harness_context_artifacts",
```

Before `row = { ... }`, add:

```python
                harness_components = result.get("harness_components", {}) or {}
```

Add row entries:

```python
                    "experiment_id": result.get("experiment_id", ""),
                    "cell_id": result.get("cell_id", ""),
                    "agent_cli": result.get("agent_cli", result.get("agent", "")),
                    "orchestration_id": result.get("orchestration_id", ""),
                    "orchestration_source_type": result.get("orchestration_source_type", ""),
                    "task_set": result.get("task_set", ""),
                    "harness_preset": result.get("harness_preset", ""),
                    "harness_fingerprint": result.get("harness_fingerprint", ""),
                    "harness_instruction_files": json.dumps(harness_components.get("instruction_files", [])),
                    "harness_skills": json.dumps(harness_components.get("skills", [])),
                    "harness_mcp_servers": json.dumps(harness_components.get("mcp_servers", [])),
                    "harness_hooks": json.dumps(harness_components.get("hooks", [])),
                    "harness_cli_config": json.dumps(harness_components.get("cli_config", [])),
                    "harness_tool_wrappers": json.dumps(harness_components.get("tool_wrappers", [])),
                    "harness_context_artifacts": json.dumps(harness_components.get("context_artifacts", [])),
```

- [ ] **Step 6: Run tests to verify GREEN**

Run:

```powershell
uv run pytest tests/test_benchmark_runner_metadata.py -q
```

Expected: PASS, `5 passed`.

- [ ] **Step 7: Run Task 1 and Task 2 tests together**

Run:

```powershell
uv run pytest tests/test_experiment_config.py tests/test_benchmark_runner_metadata.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

Run:

```powershell
git add gamedevbench/src/benchmark_runner.py tests/test_benchmark_runner_metadata.py
git commit -m "feat(benchmark): record factorial run metadata"
```

---

### Task 3: CLI Alias and Single-Cell Dry Run

**Files:**
- Modify: `gamedevbench/src/benchmark_runner.py`
- Create: `tests/test_cli_experiment.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli_experiment.py` with:

```python
import sys

import pytest

from gamedevbench.src import benchmark_runner


def run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["benchmark_runner.py", *argv])
    benchmark_runner.main()


def test_agent_cli_conflict_exits_with_error(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        run_main(
            monkeypatch,
            [
                "--agent",
                "codex",
                "--agent-cli",
                "claude-code",
                "run",
                "--dry-run",
                "--task-list",
                "test_task.yaml",
            ],
        )

    assert exc.value.code == 2
    assert "--agent and --agent-cli disagree" in capsys.readouterr().err


def test_legacy_agent_alias_dry_run(monkeypatch, capsys):
    run_main(
        monkeypatch,
        [
            "--agent",
            "codex",
            "--model",
            "gpt-5.4",
            "--harness",
            "gd-baseline",
            "--orchestration",
            "single-agent",
            "run",
            "--dry-run",
            "--task-list",
            "test_task.yaml",
        ],
    )

    output = capsys.readouterr().out
    assert "DRY RUN" in output
    assert "agent_cli: codex" in output
    assert "harness_preset: gd-baseline" in output
    assert "orchestration: single-agent" in output


def test_agent_cli_dry_run(monkeypatch, capsys):
    run_main(
        monkeypatch,
        [
            "--agent-cli",
            "codex",
            "--model",
            "gpt-5.4",
            "--harness",
            "gd-baseline",
            "--orchestration",
            "single-agent",
            "run",
            "--dry-run",
            "--task-list",
            "test_task.yaml",
        ],
    )

    output = capsys.readouterr().out
    assert "DRY RUN" in output
    assert "cell_id:" in output
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
uv run pytest tests/test_cli_experiment.py -q
```

Expected: FAIL because `--agent-cli`, `--harness`, `--orchestration`, and `--dry-run` do not exist.

- [ ] **Step 3: Add CLI arguments and alias validation**

In `main()`, add a parser argument after `--agent`:

```python
    parser.add_argument(
        "--agent-cli",
        choices=SolverFactory.get_available_agents(),
        help="Agent CLI to use for solving tasks; preferred alias for --agent",
    )
```

Add parser arguments after `--model`:

```python
    parser.add_argument(
        "--harness",
        default="gd-baseline",
        help="Harness preset name for single-cell runs",
    )
    parser.add_argument(
        "--orchestration",
        default="single-agent",
        help="Orchestration id for single-cell runs",
    )
```

Add run subparser argument:

```python
    run_parser.add_argument(
        "--dry-run",
        help="Resolve configuration and print planned cells without invoking Godot or agent CLIs",
        action="store_true",
    )
```

After `args = parser.parse_args()`, add:

```python
    if args.agent and args.agent_cli and args.agent != args.agent_cli:
        parser.error("--agent and --agent-cli disagree")
    resolved_agent_cli = args.agent_cli or args.agent
```

- [ ] **Step 4: Build single-cell config and dry-run output**

Add import:

```python
from gamedevbench.src.experiment_config import build_single_cell
```

Before constructing `GodotBenchmarkRunner`, add:

```python
    task_list_file = getattr(args, "task_list", None)
    single_cell = None
    if resolved_agent_cli:
        single_cell = build_single_cell(
            agent_cli=resolved_agent_cli,
            model=args.model,
            harness_name=args.harness,
            orchestration_id=args.orchestration,
            task_list_file=task_list_file,
        )
```

Replace runner construction with:

```python
    runner = GodotBenchmarkRunner(
        use_gt=args.gt,
        agent=resolved_agent_cli,
        agent_cli=resolved_agent_cli,
        model=args.model,
        debug=args.debug,
        resume=args.resume,
        use_mcp=args.enable_mcp,
        resume_from=args.resume_from if hasattr(args, "resume_from") else None,
        skip_display=args.skip_display,
        use_runtime_video=args.use_runtime_video,
        run_name=args.run_name,
        experiment_id=single_cell.experiment_id if single_cell else None,
        cell_id=single_cell.cell_id if single_cell else None,
        task_set=single_cell.task_set if single_cell else None,
        orchestration=single_cell.orchestration if single_cell else None,
        harness=single_cell.harness if single_cell else None,
    )
```

In `elif args.command == "run":`, before running tasks, add:

```python
        if args.dry_run:
            print("DRY RUN")
            print(f"agent_cli: {runner.agent_cli}")
            print(f"model: {runner.model}")
            print(f"cell_id: {runner.cell_id}")
            print(f"task_set: {runner.task_set}")
            print(f"harness_preset: {runner.harness.preset}")
            print(f"harness_fingerprint: {runner.harness.fingerprint}")
            print(f"orchestration: {runner.orchestration.id}")
            print(f"orchestration_source_type: {runner.orchestration.source_type}")
            return
```

- [ ] **Step 5: Run CLI tests to verify GREEN**

Run:

```powershell
uv run pytest tests/test_cli_experiment.py -q
```

Expected: PASS, `3 passed`.

- [ ] **Step 6: Run direct CLI smoke**

Run:

```powershell
uv run python gamedevbench/src/benchmark_runner.py --agent codex --model gpt-5.4 --harness gd-baseline --orchestration single-agent run --task-list test_task.yaml --dry-run
```

Expected output contains:

```text
DRY RUN
agent_cli: codex
model: gpt-5.4
harness_preset: gd-baseline
orchestration: single-agent
```

- [ ] **Step 7: Commit Task 3**

Run:

```powershell
git add gamedevbench/src/benchmark_runner.py tests/test_cli_experiment.py
git commit -m "feat(benchmark): add factorial single-cell CLI"
```

---

### Task 4: Experiment YAML Execution and Per-Cell Directories

**Files:**
- Modify: `gamedevbench/src/benchmark_runner.py`
- Modify: `tests/test_cli_experiment.py`
- Create: `experiments/gd-harness-ab-smoke.yaml`

- [ ] **Step 1: Add failing experiment dry-run test**

Append to `tests/test_cli_experiment.py`:

```python
def test_experiment_dry_run_lists_cells(monkeypatch, tmp_path, capsys):
    experiment_file = tmp_path / "experiment.yaml"
    experiment_file.write_text(
        """
id: gd-harness-ab
task_sets:
  smoke:
    task_list: test_task.yaml
harness_presets:
  gd-baseline:
    components: {}
  gd-skill:
    components:
      skills: [godot-debugging]
cells:
  - id: baseline
    model: gpt-5.4
    agent_cli: codex
    orchestration:
      id: single-agent
      source_type: builtin
    task_set: smoke
    harness:
      preset: gd-baseline
  - id: skill
    model: gpt-5.4
    agent_cli: codex
    orchestration:
      id: single-agent
      source_type: builtin
    task_set: smoke
    harness:
      preset: gd-skill
"""
    )

    run_main(monkeypatch, ["run", "--experiment", str(experiment_file), "--dry-run"])

    output = capsys.readouterr().out
    assert "DRY RUN EXPERIMENT gd-harness-ab" in output
    assert "cell: baseline" in output
    assert "cell: skill" in output
    assert "harness_skills: ['godot-debugging']" in output
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
uv run pytest tests/test_cli_experiment.py::test_experiment_dry_run_lists_cells -q
```

Expected: FAIL because `run --experiment` is not implemented.

- [ ] **Step 3: Add experiment parser argument**

In `run_parser`, add:

```python
    run_parser.add_argument(
        "--experiment",
        help="Path to an experiment YAML file containing one or more cells",
    )
```

Add import:

```python
from gamedevbench.src.experiment_config import load_experiment_file
```

- [ ] **Step 4: Implement experiment dry-run and execution helper**

Add function above `main()`:

```python
def run_experiment(args) -> List[Dict]:
    experiment = load_experiment_file(args.experiment)
    if args.dry_run:
        print(f"DRY RUN EXPERIMENT {experiment.experiment_id}")
        for cell in experiment.cells:
            print(f"cell: {cell.cell_id}")
            print(f"  agent_cli: {cell.agent_cli}")
            print(f"  model: {cell.model}")
            print(f"  task_set: {cell.task_set}")
            print(f"  task_list_file: {cell.task_list_file}")
            print(f"  harness_preset: {cell.harness.preset}")
            print(f"  harness_fingerprint: {cell.harness.fingerprint}")
            print(f"  harness_skills: {cell.harness.components.get('skills', [])}")
            print(f"  orchestration: {cell.orchestration.id}")
            print(f"  orchestration_source_type: {cell.orchestration.source_type}")
        return []

    results = []
    for cell in experiment.cells:
        runner = GodotBenchmarkRunner(
            use_gt=args.gt,
            agent=cell.agent_cli,
            agent_cli=cell.agent_cli,
            model=cell.model,
            debug=args.debug,
            resume=args.resume,
            use_mcp=args.enable_mcp,
            resume_from=args.resume_from if hasattr(args, "resume_from") else None,
            skip_display=args.skip_display,
            use_runtime_video=args.use_runtime_video,
            run_name=args.run_name,
            experiment_id=cell.experiment_id,
            cell_id=cell.cell_id,
            task_set=cell.task_set,
            orchestration=cell.orchestration,
            harness=cell.harness,
        )
        result = runner.run_all_tasks(task_list_file=cell.task_list_file)
        results.append(result)
    return results
```

In `elif args.command == "run":`, before single task handling, add:

```python
        if args.experiment:
            result = run_experiment(args)
            if not args.dry_run:
                print(json.dumps(result, indent=2))
            return
```

- [ ] **Step 5: Run experiment dry-run test to verify GREEN**

Run:

```powershell
uv run pytest tests/test_cli_experiment.py::test_experiment_dry_run_lists_cells -q
```

Expected: PASS.

- [ ] **Step 6: Add example experiment file**

Create `experiments/gd-harness-ab-smoke.yaml`:

```yaml
id: gd-harness-ab-smoke
description: Smoke experiment for baseline vs skill-only harness metadata.

task_sets:
  smoke:
    task_list: test_task.yaml

harness_presets:
  gd-baseline:
    components: {}
  gd-skill-only:
    components:
      skills: [godot-debugging]

cells:
  - id: gpt54_codex_baseline_single_smoke
    model: gpt-5.4
    agent_cli: codex
    orchestration:
      id: single-agent
      source_type: builtin
    task_set: smoke
    harness:
      preset: gd-baseline

  - id: gpt54_codex_skill_single_smoke
    model: gpt-5.4
    agent_cli: codex
    orchestration:
      id: single-agent
      source_type: builtin
    task_set: smoke
    harness:
      preset: gd-skill-only
```

- [ ] **Step 7: Run full CLI test file**

Run:

```powershell
uv run pytest tests/test_cli_experiment.py -q
```

Expected: PASS, `4 passed`.

- [ ] **Step 8: Run example experiment dry-run**

Run:

```powershell
uv run python gamedevbench/src/benchmark_runner.py run --experiment experiments/gd-harness-ab-smoke.yaml --dry-run
```

Expected output contains both:

```text
cell: gpt54_codex_baseline_single_smoke
cell: gpt54_codex_skill_single_smoke
```

- [ ] **Step 9: Commit Task 4**

Run:

```powershell
git add gamedevbench/src/benchmark_runner.py tests/test_cli_experiment.py experiments/gd-harness-ab-smoke.yaml
git commit -m "feat(benchmark): support experiment yaml cells"
```

---

### Task 5: README Usage and End-to-End Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README section**

In `README.md`, after the existing `Usage` options table, add:

```markdown
### Factorial Experiments

GameDevBench can also run factorial experiment cells over:

```text
model × agent_cli × orchestration × task_set × harness_components
```

`agent_cli` is the execution surface (`codex`, `claude-code`, `gemini-cli`, `openhands`). `custom_harness` is resolved from concrete component types:

```yaml
instruction_files: []
skills: []
mcp_servers: []
hooks: []
cli_config: []
tool_wrappers: []
context_artifacts: []
```

Named harness presets are shortcuts. Results record the resolved components and `harness_fingerprint`.

Run a single cell:

```bash
uv run python gamedevbench/src/benchmark_runner.py \
  --agent-cli codex \
  --model gpt-5.4 \
  --harness gd-baseline \
  --orchestration single-agent \
  run --task-list test_task.yaml
```

Run an experiment YAML:

```bash
uv run python gamedevbench/src/benchmark_runner.py run \
  --experiment experiments/gd-harness-ab-smoke.yaml
```

Legacy `--agent` remains supported as an alias for `--agent-cli`.
```

- [ ] **Step 2: Run targeted test suite**

Run:

```powershell
uv run pytest tests/test_experiment_config.py tests/test_benchmark_runner_metadata.py tests/test_cli_experiment.py -q
```

Expected: PASS.

- [ ] **Step 3: Run help smoke**

Run:

```powershell
uv run python gamedevbench/src/benchmark_runner.py --help
```

Expected output contains:

```text
--agent-cli
--harness
--orchestration
```

- [ ] **Step 4: Run legacy single-cell dry-run**

Run:

```powershell
uv run python gamedevbench/src/benchmark_runner.py --agent codex --model gpt-5.4 --harness gd-baseline --orchestration single-agent run --task-list test_task.yaml --dry-run
```

Expected output contains:

```text
DRY RUN
agent_cli: codex
harness_preset: gd-baseline
orchestration: single-agent
```

- [ ] **Step 5: Run experiment dry-run**

Run:

```powershell
uv run python gamedevbench/src/benchmark_runner.py run --experiment experiments/gd-harness-ab-smoke.yaml --dry-run
```

Expected output contains:

```text
DRY RUN EXPERIMENT gd-harness-ab-smoke
cell: gpt54_codex_baseline_single_smoke
cell: gpt54_codex_skill_single_smoke
```

- [ ] **Step 6: Check git status for unrelated changes**

Run:

```powershell
git status --short
```

Expected: implementation files changed or committed by this plan only. The pre-existing unrelated items may still appear:

```text
 M gamedevbench/src/utils/constants.py
?? docs/tokenrouter-models.md
?? tests/test_constants.py
```

Do not stage those unrelated files in this task.

- [ ] **Step 7: Commit Task 5**

Run:

```powershell
git add README.md
git commit -m "docs(benchmark): document factorial experiment usage"
```

---

## Self-Review Checklist

- Spec coverage:
  - Five dimensions are represented by `ExperimentCell`.
  - Seven custom harness component categories are represented by `COMPONENT_KEYS`.
  - Named presets resolve into components plus fingerprint.
  - CLI and experiment YAML entrypoints are both covered.
  - `agentic_workflow` and `scripted_workflow` enter schema through `OrchestrationSpec`, while first implementation only supports `single-agent`.
- Placeholder scan:
  - This plan contains no `TBD`, no open-ended "fill in details", and no step that says to write unspecified tests.
- Type consistency:
  - `agent_cli`, `experiment_id`, `cell_id`, `task_set`, `harness_preset`, `harness_components`, `harness_fingerprint`, `orchestration_id`, and `orchestration_source_type` are used consistently across config, runner, manifest, result JSON, and CSV.
