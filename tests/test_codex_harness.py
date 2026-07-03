from __future__ import annotations

import textwrap

from aigamedevbench.codex_harness import materialize_codex_harness
from aigamedevbench.experiment_config import OrchestrationSpec, resolve_harness, ExperimentCell


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
        harness=resolve_harness({"preset": preset}),
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
            approval_policy = "never"
            sandbox_mode = "danger-full-access"

            [features]
            hooks = true
            memories = true

            [mcp_servers.fast_context]
            command = "fast-context"
            """
        ),
        encoding="utf-8",
    )
    (home / "AGENTS.md").write_text("custom instruction\n", encoding="utf-8")
    (home / "hooks.json").write_text('{"hooks":[]}\n', encoding="utf-8")
    (home / "skills").mkdir()
    (home / "skills" / "agentic-game-development").mkdir()
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
    assert "mcp_servers.fast_context" not in (runtime.codex_home / "config.toml").read_text(encoding="utf-8")
    flags = runtime.manifest["probe"]["visibility_flags"]
    assert flags["has_user_agents_md"] is False
    assert flags["has_user_skills"] is False
    assert flags["has_memory_context"] is False
    assert flags["has_mcp_servers"] is False


def test_custom_current_full_materializes_configured_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(_source_home(tmp_path)))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "homes"))

    runtime = materialize_codex_harness(_cell("custom-current-full"))

    assert runtime is not None
    assert (runtime.codex_home / "AGENTS.md").exists()
    assert (runtime.codex_home / "hooks.json").exists()
    assert (runtime.codex_home / "skills" / "agentic-game-development").exists()
    assert (runtime.codex_home / "memories" / "memory_summary.md").exists()
    config = (runtime.codex_home / "config.toml").read_text(encoding="utf-8")
    assert 'model_reasoning_effort = "low"' in config
    assert 'model = "gpt-5.5"' in config
    assert "mcp_servers.fast_context" in config
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
        harness=resolve_harness({"preset": "custom-current-full"}),
    )

    assert materialize_codex_harness(cell) is None


def test_custom_current_full_uses_plugin_cache_for_broken_skill_link(tmp_path, monkeypatch):
    monkeypatch.setenv("AIGDB_CODEX_SOURCE_HOME", str(_source_home_with_broken_skill_link(tmp_path)))
    monkeypatch.setenv("AIGDB_CODEX_HOME_ROOT", str(tmp_path / "homes"))

    runtime = materialize_codex_harness(_cell("custom-current-full"))

    assert runtime is not None
    assert (runtime.codex_home / "skills" / "agentic-game-development" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "fallback skill\n"
    assert runtime.manifest["probe"]["visibility_flags"]["has_user_skills"] is True
