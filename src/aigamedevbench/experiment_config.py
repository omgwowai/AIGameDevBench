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
    "skills",
    "mcp_servers",
    "hooks",
    "cli_config",
    "tool_wrappers",
    "context_artifacts",
]


DEFAULT_HARNESS_PRESETS: dict[str, dict[str, Any]] = {
    "gd-baseline": {"components": {}},
    "bare-codex": {"components": {}},
    "custom-current-full": {
        "components": {
            "instruction_files": ["codex-user-agents-md"],
            "skills": ["agentic-game-development"],
            "mcp_servers": ["fast-context"],
            "hooks": ["codex-user-hooks"],
            "cli_config": ["codex-full-auto"],
            "context_artifacts": ["codex-memory-summary"],
        }
    },
}


@dataclass(frozen=True)
class ResolvedHarness:
    preset: str | None
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


def _merge_components(
    base: dict[str, Any],
    additions: dict[str, Any] | None,
    removals: dict[str, Any] | None,
) -> dict[str, list[str]]:
    merged = normalize_components(base)
    for key, values in normalize_components(additions).items():
        merged[key] = sorted(set(merged[key]) | set(values))
    for key, values in normalize_components(removals).items():
        merged[key] = sorted(set(merged[key]) - set(values))
    return normalize_components(merged)


def resolve_harness(
    harness_spec: dict[str, Any] | None,
    harness_presets: dict[str, Any] | None = None,
) -> ResolvedHarness:
    harness_spec = harness_spec or {}
    presets = {**DEFAULT_HARNESS_PRESETS, **(harness_presets or {})}
    preset_name = harness_spec.get("preset")

    base: dict[str, Any] = {}
    if preset_name:
        if preset_name not in presets:
            raise ValueError(f"Unknown harness preset: {preset_name}")
        base = (presets[preset_name] or {}).get("components", {})
    if "components" in harness_spec:
        base = harness_spec["components"]

    components = _merge_components(base, harness_spec.get("add"), harness_spec.get("remove"))
    return ResolvedHarness(
        preset=preset_name,
        components=components,
        fingerprint=fingerprint_components(components),
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is None:
        raise RuntimeError("PyYAML is required for experiment YAML files")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("Experiment YAML must contain a mapping at the root")
    return data


def load_experiment_file(path: str | Path) -> ExperimentSpec:
    experiment_path = Path(path)
    data = _load_yaml(experiment_path)
    experiment_id = sanitize_identifier(str(data.get("id", experiment_path.stem)))
    description = str(data.get("description", ""))
    task_sets = data.get("task_sets", {}) or {}
    harness_presets = data.get("harness_presets", {}) or {}
    cells: list[ExperimentCell] = []

    for raw_cell in data.get("cells", []) or []:
        task_set_name = str(raw_cell.get("task_set", "filtered30"))
        task_set = task_sets.get(task_set_name, {}) or {}
        testcases_dir = raw_cell.get("testcases_dir") or task_set.get(
            "testcases_dir", "testcases_filtered"
        )
        testcase_ids = raw_cell.get("testcases") or task_set.get("testcases") or []
        if testcase_ids is None:
            testcase_ids = []
        if not isinstance(testcase_ids, list):
            raise ValueError(f"Task set '{task_set_name}' testcases must be a list")

        cells.append(
            ExperimentCell(
                experiment_id=experiment_id,
                cell_id=sanitize_identifier(str(raw_cell["id"])),
                model=str(raw_cell.get("model", "default")),
                model_reasoning_effort=(
                    str(raw_cell["model_reasoning_effort"])
                    if raw_cell.get("model_reasoning_effort") is not None
                    else None
                ),
                agent_cli=str(raw_cell.get("agent_cli", "manual")),
                orchestration=OrchestrationSpec.from_value(raw_cell.get("orchestration")),
                task_set=task_set_name,
                testcases_dir=str(testcases_dir),
                testcase_ids=[str(testcase_id) for testcase_id in testcase_ids],
                harness=resolve_harness(raw_cell.get("harness"), harness_presets),
            )
        )

    return ExperimentSpec(experiment_id=experiment_id, description=description, cells=cells)
