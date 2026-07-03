from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aigamedevbench.experiment_config import ExperimentCell

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


@dataclass(frozen=True)
class CodexHarnessRuntime:
    codex_home: Path
    env: dict[str, str]
    manifest: dict[str, Any]


def materialize_codex_harness(cell: ExperimentCell) -> CodexHarnessRuntime | None:
    if cell.agent_cli.lower() != "codex":
        return None

    source_home = Path(
        os.environ.get("AIGDB_CODEX_SOURCE_HOME")
        or os.environ.get("CODEX_HOME")
        or (Path.home() / ".codex")
    )
    homes_root = Path(
        os.environ.get("AIGDB_CODEX_HOME_ROOT")
        or (Path.home() / ".codex-bench" / "homes")
    )
    homes_root.mkdir(parents=True, exist_ok=True)
    fingerprint_prefix = cell.harness.fingerprint.split(":", 1)[-1][:12]
    effort = cell.model_reasoning_effort or "default"
    preset = cell.harness.preset or "components"
    codex_home = homes_root / f"{_safe_name(preset)}-{_safe_name(effort)}-{fingerprint_prefix}"
    _reset_directory(codex_home, homes_root)

    warnings: list[str] = []
    _materialize_common(source_home, codex_home)
    if _has_any_component(cell, "instruction_files"):
        _copy_path(source_home / "AGENTS.md", codex_home / "AGENTS.md")
    if _has_any_component(cell, "hooks"):
        _copy_path(source_home / "hooks.json", codex_home / "hooks.json")
    if _has_any_component(cell, "skills"):
        warnings.extend(_copy_named_children(
            source_home / "skills",
            codex_home / "skills",
            cell.harness.components["skills"],
            source_home,
        ))
    if _has_any_component(cell, "context_artifacts"):
        _copy_memory_artifacts(source_home, codex_home)

    _write_config(source_home, codex_home, cell)
    probe = _probe_codex_prompt_input(codex_home)
    manifest = {
        "codex_home": str(codex_home),
        "harness_preset": cell.harness.preset,
        "harness_fingerprint": cell.harness.fingerprint,
        "model_reasoning_effort": cell.model_reasoning_effort,
        "probe": probe,
    }
    if warnings:
        manifest["warnings"] = warnings
    return CodexHarnessRuntime(
        codex_home=codex_home,
        env={"CODEX_HOME": str(codex_home)},
        manifest=manifest,
    )


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "default"


def _reset_directory(path: Path, root: Path) -> None:
    root = root.resolve()
    parent = path.parent.resolve()
    if parent != root:
        raise ValueError(f"Refusing to reset CODEX_HOME outside bench root: {path}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _has_any_component(cell: ExperimentCell, key: str) -> bool:
    return bool(cell.harness.components.get(key))


def _copy_path(src: Path, dst: Path) -> None:
    if not src.exists() and not src.is_symlink():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    effective_src = src.resolve(strict=False) if src.is_symlink() else src
    if not effective_src.exists():
        return
    if effective_src.is_dir():
        shutil.copytree(effective_src, dst, dirs_exist_ok=True)
    elif effective_src.is_file():
        shutil.copy2(effective_src, dst)


def _copy_named_children(
    src_root: Path,
    dst_root: Path,
    names: list[str],
    source_home: Path,
) -> list[str]:
    warnings: list[str] = []
    for name in names:
        src = _resolve_named_component(src_root, name, source_home)
        if src is None:
            warnings.append(f"skill '{name}' not found under {src_root}")
            continue
        _copy_path(src, dst_root / name)
    return warnings


def _resolve_named_component(src_root: Path, name: str, source_home: Path) -> Path | None:
    src = src_root / name
    if src.exists():
        return src
    if src.is_symlink():
        resolved = src.resolve(strict=False)
        if resolved.exists():
            return resolved
    cache_root = source_home / "plugins" / "cache"
    if cache_root.is_dir():
        for skill_md in cache_root.rglob(f"skills/{name}/SKILL.md"):
            return skill_md.parent
    return None


def _materialize_common(source_home: Path, codex_home: Path) -> None:
    _copy_path(source_home / "auth.json", codex_home / "auth.json")


def _copy_memory_artifacts(source_home: Path, codex_home: Path) -> None:
    src_memories = source_home / "memories"
    dst_memories = codex_home / "memories"
    for name in ("memory_summary.md", "MEMORY.md"):
        _copy_path(src_memories / name, dst_memories / name)


def _write_config(source_home: Path, codex_home: Path, cell: ExperimentCell) -> None:
    source_config = source_home / "config.toml"
    if cell.harness.components == _empty_components():
        text = _bare_config_text(source_config, cell)
    else:
        text = source_config.read_text(encoding="utf-8", errors="replace") if source_config.exists() else ""
        text = _set_top_level_toml_value(text, "model", cell.model)
        if cell.model_reasoning_effort:
            text = _set_top_level_toml_value(
                text, "model_reasoning_effort", cell.model_reasoning_effort
            )
        text = re.sub(
            r"(?m)^CODEX_HOME\s*=.*$",
            lambda _: f"CODEX_HOME = {_toml_value(str(codex_home))}",
            text,
        )
    (codex_home / "config.toml").write_text(text, encoding="utf-8")


def _empty_components() -> dict[str, list[str]]:
    from aigamedevbench.experiment_config import COMPONENT_KEYS

    return {key: [] for key in COMPONENT_KEYS}


def _bare_config_text(source_config: Path, cell: ExperimentCell) -> str:
    source_data = _load_toml(source_config)
    keep_keys = (
        "model_provider",
        "openai_base_url",
        "model_catalog_json",
        "disable_response_storage",
        "approval_policy",
        "sandbox_mode",
        "service_tier",
    )
    lines: list[str] = []
    for key in keep_keys:
        if key in source_data:
            lines.append(f"{key} = {_toml_value(source_data[key])}")
    lines.append(f"model = {_toml_value(cell.model)}")
    if cell.model_reasoning_effort:
        lines.append(f"model_reasoning_effort = {_toml_value(cell.model_reasoning_effort)}")
    lines.extend(
        [
            "",
            "[features]",
            "hooks = false",
            "memories = false",
            "child_agents_md = false",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _set_top_level_toml_value(text: str, key: str, value: Any) -> str:
    lines = text.splitlines()
    first_section = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    preamble = lines[:first_section]
    rest = lines[first_section:]
    replacement = f"{key} = {_toml_value(value)}"
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(preamble):
        if pattern.match(line):
            preamble[i] = replacement
            break
    else:
        preamble.append(replacement)
    return "\n".join([*preamble, *rest]).rstrip() + "\n"


def _probe_codex_prompt_input(codex_home: Path) -> dict[str, Any]:
    flags = _visibility_flags(codex_home)
    env = {**os.environ, "CODEX_HOME": str(codex_home)}
    try:
        proc = subprocess.run(
            ["codex", "debug", "prompt-input", "AIGameDevBench harness probe"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=env,
        )
        stdout = proc.stdout or ""
        stderr_tail = "\n".join((proc.stderr or "").splitlines()[-5:])
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "exit_code": proc.returncode,
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            "stdout_bytes": len(stdout.encode("utf-8")),
            "stderr_tail": stderr_tail,
            "visibility_flags": flags,
        }
    except FileNotFoundError:
        return {
            "status": "unavailable",
            "exit_code": None,
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stdout_bytes": 0,
            "stderr_tail": "codex binary not found",
            "visibility_flags": flags,
        }
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout or ""
        if isinstance(stdout, bytes):
            stdout_bytes = stdout
        else:
            stdout_bytes = stdout.encode("utf-8")
        return {
            "status": "timeout",
            "exit_code": None,
            "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
            "stdout_bytes": len(stdout_bytes),
            "stderr_tail": "codex debug prompt-input timed out",
            "visibility_flags": flags,
        }


def _visibility_flags(codex_home: Path) -> dict[str, bool]:
    config_text = ""
    config_path = codex_home / "config.toml"
    if config_path.exists():
        config_text = config_path.read_text(encoding="utf-8", errors="replace")
    skills_dir = codex_home / "skills"
    memories_dir = codex_home / "memories"
    user_skill_entries = [
        path for path in skills_dir.iterdir()
        if path.name != ".system"
    ] if skills_dir.is_dir() else []
    return {
        "has_user_agents_md": (codex_home / "AGENTS.md").is_file(),
        "has_user_skills": bool(user_skill_entries),
        "has_mcp_servers": "[mcp_servers." in config_text,
        "has_hooks": (codex_home / "hooks.json").is_file() or "hooks = true" in config_text,
        "has_memory_context": any(
            (memories_dir / name).is_file()
            for name in ("memory_summary.md", "MEMORY.md")
        ),
        "has_cli_config": config_path.is_file(),
    }
