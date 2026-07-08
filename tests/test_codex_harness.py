from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

from aigamedevbench.codex_harness import (
    _reset_directory,
    materialize_attempt_codex_runtime,
    materialize_codex_harness,
)
from aigamedevbench.experiment_config import OrchestrationSpec, resolve_harness, ExperimentCell


HARNESS_CATALOG = {
    "bare-codex": {"target_agent_cli": "codex", "components": {}},
    "all-codex": {
        "target_agent_cli": "codex",
        "components": {
            "instruction_files": ["codex-user-agents-md"],
            "plugins": ["agentic-game-development-superpowers"],
            "skills": ["fast-context", "transcript-viewer"],
            "mcp_servers": ["node_repl"],
            "hooks": ["codex-user-hooks"],
            "cli_config": ["codex-full-auto"],
            "context_artifacts": ["codex-memory-summary"],
        }
    },
}


def _cell(preset: str) -> ExperimentCell:
    return ExperimentCell(
        experiment_id="exp",
        cell_id=preset,
        model="gpt-5.5",
        model_reasoning_effort="low",
        agent_cli="codex",
        orchestration=OrchestrationSpec(id="single-agent", source_type="builtin"),
        task_set="filtered30",
        testcases_dir="testcases_filtered",
        testcase_ids=["case-a"],
        harness=resolve_harness(preset, HARNESS_CATALOG),
    )


def _cell_with_id(preset: str, cell_id: str) -> ExperimentCell:
    cell = _cell(preset)
    return ExperimentCell(
        experiment_id=cell.experiment_id,
        cell_id=cell_id,
        model=cell.model,
        model_reasoning_effort=cell.model_reasoning_effort,
        agent_cli=cell.agent_cli,
        orchestration=cell.orchestration,
        task_set=cell.task_set,
        testcases_dir=cell.testcases_dir,
        testcase_ids=cell.testcase_ids,
        harness=cell.harness,
    )


def _source_home(tmp_path):
    home = tmp_path / "source"
    home.mkdir()
    (home / "auth.json").write_text('{"token":"fake"}\n', encoding="utf-8")
    (home / "config.toml").write_text(
        textwrap.dedent(
            """
            model_provider = "openai"
            openai_base_url = "http://127.0.0.1:8462/v1/"
            model = "gpt-5.5"
            model_reasoning_effort = "xhigh"
            model_auto_compact_token_limit = 550000
            model_catalog_json = 'C:\\Users\\CedricChen\\.codex\\models-1m.json'
            model_context_window = 1000000
            approval_policy = "never"
            sandbox_mode = "danger-full-access"
            service_tier = "priority"
            disable_response_storage = true

            [features]
            hooks = true
            memories = true

            [mcp_servers.fast_context]
            command = "fast-context"

            [mcp_servers.node_repl]
            command = "node-repl"
            """
        ),
        encoding="utf-8",
    )
    (home / "AGENTS.md").write_text("custom instruction\n", encoding="utf-8")
    (home / "hooks.json").write_text('{"hooks":[]}\n', encoding="utf-8")
    (home / "skills").mkdir()
    (home / "skills" / "agentic-game-development").mkdir()
    (home / "skills" / "fast-context").mkdir()
    (home / "skills" / "transcript-viewer").mkdir()
    plugin = (
        home
        / "plugins"
        / "cache"
        / "local"
        / "agentic-game-development-superpowers"
        / "0.1.0"
    )
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "skills" / "agentic-game-development").mkdir(parents=True)
    (plugin / "hooks").mkdir()
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        '{"name":"agentic-game-development-superpowers"}\n',
        encoding="utf-8",
    )
    (plugin / "hooks" / "hooks-codex.json").write_text('{"hooks":[]}\n', encoding="utf-8")
    (home / "memories").mkdir()
    (home / "memories" / "memory_summary.md").write_text("memory\n", encoding="utf-8")
    return home


def _source_home_with_broken_skill_link(tmp_path):
    home = _source_home(tmp_path)
    skill_link = home / "skills" / "agentic-game-development"
    if skill_link.exists():
        if skill_link.is_dir():
            skill_link.rmdir()
        else:
            skill_link.unlink()
    skill_link.symlink_to(tmp_path / "missing-skill-target", target_is_directory=True)
    fallback = home / "plugins" / "cache" / "bundle" / "skills" / "agentic-game-development"
    fallback.mkdir(parents=True)
    (fallback / "SKILL.md").write_text("fallback skill\n", encoding="utf-8")
    return home


def test_bare_codex_materializes_minimal_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(_source_home(tmp_path)))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "homes"))

    runtime = materialize_codex_harness(_cell("bare-codex"))

    assert runtime is not None
    assert runtime.env == {"CODEX_HOME": str(runtime.codex_home)}
    assert (runtime.codex_home / "auth.json").exists()
    assert not (runtime.codex_home / "AGENTS.md").exists()
    assert not (runtime.codex_home / "hooks.json").exists()
    assert not (runtime.codex_home / "skills" / "agentic-game-development").exists()
    config = (runtime.codex_home / "config.toml").read_text(encoding="utf-8")
    assert 'model_catalog_json = "C:\\\\Users\\\\CedricChen\\\\.codex\\\\models-1m.json"' in config
    assert "model_context_window = 1000000" in config
    assert "model_auto_compact_token_limit = 550000" in config
    assert "mcp_servers.fast_context" not in config
    flags = runtime.manifest["probe"]["visibility_flags"]
    assert flags["has_user_agents_md"] is False
    assert flags["has_user_skills"] is False
    assert flags["has_memory_context"] is False
    assert flags["has_mcp_servers"] is False


def test_all_codex_materializes_configured_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(_source_home(tmp_path)))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "homes"))

    runtime = materialize_codex_harness(_cell("all-codex"))

    assert runtime is not None
    assert (runtime.codex_home / "AGENTS.md").exists()
    assert (runtime.codex_home / "hooks.json").exists()
    assert (runtime.codex_home / "skills" / "fast-context").exists()
    assert (runtime.codex_home / "skills" / "transcript-viewer").exists()
    assert (
        runtime.codex_home
        / "plugins"
        / "cache"
        / "agentic-game-development"
        / "agentic-game-development-superpowers"
        / "0.1.0"
        / ".codex-plugin"
        / "plugin.json"
    ).exists()
    assert (
        runtime.codex_home
        / "managed-marketplaces"
        / "agd"
        / "plugins"
        / "agentic-game-development-superpowers"
        / ".codex-plugin"
        / "plugin.json"
    ).exists()
    assert (runtime.codex_home / "memories" / "memory_summary.md").exists()
    config = (runtime.codex_home / "config.toml").read_text(encoding="utf-8")
    assert 'model_reasoning_effort = "low"' in config
    assert 'model = "gpt-5.5"' in config
    assert "mcp_servers.node_repl" in config
    assert "mcp_servers.fast_context" not in config
    assert "[marketplaces.agentic-game-development]" in config
    assert '[plugins."agentic-game-development-superpowers@agentic-game-development"]' in config
    flags = runtime.manifest["probe"]["visibility_flags"]
    assert flags["has_user_agents_md"] is True
    assert flags["has_user_skills"] is True
    assert flags["has_memory_context"] is True
    assert flags["has_mcp_servers"] is True


def test_non_codex_cell_has_no_codex_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(_source_home(tmp_path)))
    cell = ExperimentCell(
        experiment_id="exp",
        cell_id="claude",
        model="claude-sonnet-4-5",
        model_reasoning_effort=None,
        agent_cli="claude-code",
        orchestration=OrchestrationSpec(id="single-agent", source_type="builtin"),
        task_set="filtered30",
        testcases_dir="testcases_filtered",
        testcase_ids=["case-a"],
        harness=resolve_harness("all-codex", HARNESS_CATALOG),
    )

    assert materialize_codex_harness(cell) is None


def test_all_codex_warns_when_configured_plugin_is_missing(tmp_path, monkeypatch):
    source_home = _source_home(tmp_path)
    shutil.rmtree(source_home / "plugins")
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(source_home))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "homes"))
    monkeypatch.setenv("AIGDB_AGENTIC_GAME_PLUGIN_ROOT", str(tmp_path / "missing-plugins"))

    runtime = materialize_codex_harness(_cell("all-codex"))

    assert runtime is not None
    assert "plugin 'agentic-game-development-superpowers' not found" in runtime.manifest["warnings"][0]


def test_attempt_codex_runtime_isolated_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(_source_home(tmp_path)))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "homes"))
    cell_runtime = materialize_codex_harness(_cell("all-codex"))
    attempt_dir = tmp_path / "attempt"

    attempt_runtime = materialize_attempt_codex_runtime(cell_runtime, attempt_dir)

    assert attempt_runtime is not None
    expected_home = attempt_dir.resolve() / "codex_home"
    assert attempt_runtime.codex_home == expected_home
    assert attempt_runtime.env == {"CODEX_HOME": str(expected_home)}
    assert attempt_runtime.codex_home != cell_runtime.codex_home
    assert (attempt_runtime.codex_home / "config.toml").exists()
    assert (attempt_runtime.codex_home / "AGENTS.md").exists()
    assert attempt_runtime.manifest["codex_home"] == str(expected_home)


def test_attempt_codex_runtime_resolves_relative_attempt_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(_source_home(tmp_path)))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "homes"))
    cell_runtime = materialize_codex_harness(_cell("bare-codex"))
    attempt_dir = Path("relative-attempt")

    attempt_runtime = materialize_attempt_codex_runtime(cell_runtime, attempt_dir)

    assert attempt_runtime is not None
    expected_home = attempt_dir.resolve() / "codex_home"
    assert attempt_runtime.codex_home == expected_home
    assert attempt_runtime.env == {"CODEX_HOME": str(expected_home)}
    assert attempt_runtime.manifest["codex_home"] == str(expected_home)


def test_same_codex_harness_in_different_cells_gets_distinct_template_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(_source_home(tmp_path)))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "homes"))

    first = materialize_codex_harness(_cell_with_id("bare-codex", "cell-a"))
    second = materialize_codex_harness(_cell_with_id("bare-codex", "cell-b"))

    assert first is not None
    assert second is not None
    assert first.codex_home != second.codex_home
    assert "cell-a" in str(first.codex_home)
    assert "cell-b" in str(second.codex_home)


def test_reset_directory_ignores_disappearing_files(tmp_path, monkeypatch):
    root = tmp_path / "homes"
    path = root / "cell"
    path.mkdir(parents=True)
    (path / "gone.pyc").write_text("x", encoding="utf-8")

    def flaky_rmtree(target):
        raise FileNotFoundError(str(path / "gone.pyc"))

    monkeypatch.setattr("aigamedevbench.codex_harness.shutil.rmtree", flaky_rmtree)

    _reset_directory(path, root)

    assert path.is_dir()
