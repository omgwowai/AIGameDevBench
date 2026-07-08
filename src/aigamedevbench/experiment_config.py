from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - PyYAML is optional for users.
    yaml = None


COMPONENT_KEYS = [
    "instruction_files",
    "plugins",
    "skills",
    "mcp_servers",
    "hooks",
    "cli_config",
    "tool_wrappers",
    "context_artifacts",
]


@dataclass(frozen=True)
class ResolvedHarness:
    preset: str
    target_agent_cli: str
    components: dict[str, list[str]]
    fingerprint: str


@dataclass(frozen=True)
class OrchestrationSpec:
    id: str
    source_type: str = "builtin"
    generator_agent_cli: str | None = None
    generator_model: str | None = None
    workflow_prompt: str | None = None
    workflow_file: str | None = None

    @property
    def is_supported(self) -> bool:
        return self.id == "single-agent" and self.source_type == "builtin"

    @classmethod
    def from_value(cls, value: Any) -> "OrchestrationSpec":
        if value is None:
            return cls(id="single-agent", source_type="builtin")
        if isinstance(value, str):
            return cls(id=value, source_type="builtin" if value == "single-agent" else value)
        if isinstance(value, dict):
            return cls(
                id=str(value.get("id", "single-agent")),
                source_type=str(value.get("source_type", "builtin")),
                generator_agent_cli=value.get("generator_agent_cli"),
                generator_model=value.get("generator_model"),
                workflow_prompt=value.get("workflow_prompt"),
                workflow_file=value.get("workflow_file"),
            )
        raise ValueError(f"Invalid orchestration spec: {value!r}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "source_type": self.source_type}
        for key in (
            "generator_agent_cli",
            "generator_model",
            "workflow_prompt",
            "workflow_file",
        ):
            value = getattr(self, key)
            if value:
                data[key] = value
        return data


@dataclass(frozen=True)
class ExperimentCell:
    experiment_id: str
    cell_id: str
    model: str
    agent_cli: str
    orchestration: OrchestrationSpec
    task_set: str
    testcases_dir: str
    testcase_ids: list[str]
    harness: ResolvedHarness
    testcase_timeouts: dict[str, float] | None = None
    model_reasoning_effort: str | None = None


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    description: str
    cells: list[ExperimentCell]


def sanitize_identifier(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip()).strip("._-")
    return sanitized or "default"


def normalize_components(components: dict[str, Any] | None) -> dict[str, list[str]]:
    components = components or {}
    unknown = sorted(set(components) - set(COMPONENT_KEYS))
    if unknown:
        raise ValueError(f"Unknown harness component keys: {', '.join(unknown)}")

    normalized: dict[str, list[str]] = {}
    for key in COMPONENT_KEYS:
        values = components.get(key, [])
        if values is None:
            values = []
        if not isinstance(values, list):
            raise ValueError(f"Harness component '{key}' must be a list")
        normalized[key] = sorted({str(value) for value in values})
    return normalized


def fingerprint_components(components: dict[str, list[str]]) -> str:
    payload = json.dumps(
        normalize_components(components),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_harness(target_agent_cli: str, components: dict[str, list[str]]) -> str:
    payload = json.dumps(
        {
            "target_agent_cli": target_agent_cli,
            "components": normalize_components(components),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_harness(harness_spec: Any, harnesses: dict[str, Any]) -> ResolvedHarness:
    if not isinstance(harness_spec, str) or not harness_spec.strip():
        raise ValueError("Harness spec must be a preset name")
    preset_name = harness_spec.strip()
    if preset_name not in harnesses:
        raise ValueError(f"Unknown harness preset: {preset_name}")
    raw_harness = harnesses[preset_name] or {}
    if not isinstance(raw_harness, dict):
        raise ValueError(f"Harness preset '{preset_name}' must be a mapping")
    if "target_agent_cli" not in raw_harness:
        raise ValueError(f"Harness preset '{preset_name}' must define target_agent_cli")
    target_agent_cli = str(raw_harness["target_agent_cli"]).strip().lower()
    if target_agent_cli not in {"generic", "codex", "claude-code"}:
        raise ValueError(
            f"Harness preset '{preset_name}' target_agent_cli must be one of: "
            "generic, codex, claude-code"
        )
    components = normalize_components(raw_harness.get("components", {}))
    return ResolvedHarness(
        preset=preset_name,
        target_agent_cli=target_agent_cli,
        components=components,
        fingerprint=fingerprint_harness(target_agent_cli, components),
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is None:
        raise RuntimeError("PyYAML is required for experiment YAML files")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("Experiment YAML must contain a mapping at the root")
    return data


def _resolve_config_path(experiment_path: Path, value: str) -> Path:
    config_path = Path(value)
    if config_path.is_absolute():
        return config_path
    return experiment_path.parent / config_path


def _load_test_sets(experiment_path: Path, data: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    test_set_file = data.get("test_set_file")
    if not test_set_file:
        raise ValueError("test_set_file is required for experiment YAML files")
    test_set_path = _resolve_config_path(experiment_path, str(test_set_file))
    test_set_data = _load_yaml(test_set_path)
    test_sets = test_set_data.get("test_sets", {}) or {}
    if not isinstance(test_sets, dict):
        raise ValueError(f"{test_set_path} field 'test_sets' must be a mapping")
    return test_sets, test_set_path


def _load_harnesses(experiment_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    if "harness_presets" in data:
        raise ValueError("harness_presets is no longer supported; use harness_file")
    harness_file = data.get("harness_file")
    if not harness_file:
        raise ValueError("harness_file is required for experiment YAML files")
    harness_path = _resolve_config_path(experiment_path, str(harness_file))
    harness_data = _load_yaml(harness_path)
    harnesses = harness_data.get("harnesses", {}) or {}
    if not isinstance(harnesses, dict):
        raise ValueError(f"{harness_path} field 'harnesses' must be a mapping")
    return harnesses


def _resolve_testcases_dir(test_set_path: Path, value: Any) -> str:
    testcases_path = Path(str(value))
    if testcases_path.is_absolute():
        return str(testcases_path)
    return str((test_set_path.parent / testcases_path).resolve())


def _normalize_testcases(
    task_set_name: str,
    raw_testcases: Any,
    default_timeout: Any = None,
) -> tuple[list[str], dict[str, float]]:
    if raw_testcases is None:
        return [], {}
    if not isinstance(raw_testcases, list):
        raise ValueError(f"Task set '{task_set_name}' testcases must be a list")

    testcase_ids: list[str] = []
    timeouts: dict[str, float] = {}
    default_timeout_value = None
    if default_timeout is not None:
        default_timeout_value = float(default_timeout)
        if default_timeout_value <= 0:
            raise ValueError(f"Task set '{task_set_name}' default_timeout must be > 0")
    for item in raw_testcases:
        if isinstance(item, str):
            testcase_ids.append(item)
            if default_timeout_value is not None:
                timeouts[item] = default_timeout_value
            continue
        if isinstance(item, dict):
            if "id" not in item:
                raise ValueError(f"Task set '{task_set_name}' testcase entry must define id")
            testcase_id = str(item["id"])
            testcase_ids.append(testcase_id)
            if item.get("timeout") is not None:
                timeout = float(item["timeout"])
                if timeout <= 0:
                    raise ValueError(
                        f"Task set '{task_set_name}' testcase '{testcase_id}' timeout must be > 0"
                    )
                timeouts[testcase_id] = timeout
            elif default_timeout_value is not None:
                timeouts[testcase_id] = default_timeout_value
            continue
        raise ValueError(
            f"Task set '{task_set_name}' testcases entries must be strings or mappings"
        )
    return testcase_ids, timeouts


def _validate_harness_target(cell_id: str, agent_cli: str, harness: ResolvedHarness) -> None:
    target = harness.target_agent_cli
    if target == "generic":
        return
    normalized_agent_cli = agent_cli.strip().lower()
    if normalized_agent_cli != target:
        raise ValueError(
            f"Harness '{harness.preset}' targets agent_cli '{target}' "
            f"but cell '{cell_id}' uses agent_cli '{agent_cli}'"
        )


def load_experiment_file(path: str | Path) -> ExperimentSpec:
    experiment_path = Path(path)
    data = _load_yaml(experiment_path)
    experiment_id = sanitize_identifier(str(data.get("id", experiment_path.stem)))
    description = str(data.get("description", ""))
    test_sets, test_set_path = _load_test_sets(experiment_path, data)
    harnesses = _load_harnesses(experiment_path, data)
    cells: list[ExperimentCell] = []

    for raw_cell in data.get("cells", []) or []:
        task_set_name = str(raw_cell.get("task_set", "filtered_30"))
        if task_set_name not in test_sets:
            raise ValueError(f"Unknown task set: {task_set_name}")
        task_set = test_sets[task_set_name] or {}
        if not isinstance(task_set, dict):
            raise ValueError(f"Task set '{task_set_name}' must be a mapping")
        if raw_cell.get("testcases_dir"):
            testcases_dir = str(raw_cell["testcases_dir"])
        else:
            testcases_dir = _resolve_testcases_dir(
                test_set_path, task_set.get("testcases_dir", "testcases_filtered")
            )
        raw_testcases = raw_cell.get("testcases") or task_set.get("testcases")
        default_timeout = raw_cell.get("default_timeout", task_set.get("default_timeout"))
        testcase_ids, testcase_timeouts = _normalize_testcases(
            task_set_name, raw_testcases, default_timeout
        )

        cell_id = sanitize_identifier(str(raw_cell["id"]))
        agent_cli = str(raw_cell.get("agent_cli", "manual"))
        harness = resolve_harness(raw_cell.get("harness"), harnesses)
        _validate_harness_target(cell_id, agent_cli, harness)

        cells.append(
            ExperimentCell(
                experiment_id=experiment_id,
                cell_id=cell_id,
                model=str(raw_cell.get("model", "default")),
                model_reasoning_effort=(
                    str(raw_cell["model_reasoning_effort"])
                    if raw_cell.get("model_reasoning_effort") is not None
                    else None
                ),
                agent_cli=agent_cli,
                orchestration=OrchestrationSpec.from_value(raw_cell.get("orchestration")),
                task_set=task_set_name,
                testcases_dir=str(testcases_dir),
                testcase_ids=testcase_ids,
                testcase_timeouts=testcase_timeouts,
                harness=harness,
            )
        )

    return ExperimentSpec(experiment_id=experiment_id, description=description, cells=cells)
