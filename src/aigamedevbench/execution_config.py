from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency is declared.
    yaml = None


@dataclass(frozen=True)
class ExecutionConfig:
    experiment_jobs: int = 2
    testcase_jobs: int = 2
    max_jobs: int = 4


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "experiments" / "execution.yaml"


def _as_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key} must be an integer") from e


def _validate(config: ExecutionConfig) -> ExecutionConfig:
    for key in ("experiment_jobs", "testcase_jobs", "max_jobs"):
        value = getattr(config, key)
        if value < 1:
            raise ValueError(f"{key} must be >= 1")
    return config


def load_execution_config(path: str | Path | None = None) -> ExecutionConfig:
    config_path = Path(path) if path is not None else _default_config_path()
    if not config_path.exists():
        return ExecutionConfig()
    if yaml is None:
        raise RuntimeError("PyYAML is required for execution config files")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Execution config YAML must contain a mapping at the root")
    parallelism = raw.get("parallelism", {}) or {}
    if not isinstance(parallelism, dict):
        raise ValueError("execution config field 'parallelism' must be a mapping")
    return _validate(
        ExecutionConfig(
            experiment_jobs=_as_int(parallelism, "experiment_jobs", 2),
            testcase_jobs=_as_int(parallelism, "testcase_jobs", 2),
            max_jobs=_as_int(parallelism, "max_jobs", 4),
        )
    )
