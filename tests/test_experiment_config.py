import textwrap

import pytest

from aigamedevbench.experiment_config import (
    COMPONENT_KEYS,
    OrchestrationSpec,
    fingerprint_components,
    fingerprint_harness,
    load_experiment_file,
    normalize_components,
    resolve_harness,
)


def test_normalize_components_keeps_known_keys_and_sorts_unique_values():
    components = normalize_components(
        {
            "skills": ["godot-debugging", "game-dev-verification", "godot-debugging"],
            "mcp_servers": ["godot-screenshot"],
            "plugins": ["agentic-game-development-superpowers"],
        }
    )

    assert list(components.keys()) == COMPONENT_KEYS
    assert components["plugins"] == ["agentic-game-development-superpowers"]
    assert components["skills"] == ["game-dev-verification", "godot-debugging"]
    assert components["mcp_servers"] == ["godot-screenshot"]
    assert components["hooks"] == []


def test_normalize_components_rejects_unknown_component_key():
    with pytest.raises(ValueError, match="Unknown harness component keys: browser_extensions"):
        normalize_components({"browser_extensions": ["not-a-supported-axis"]})


def test_resolve_harness_uses_named_catalog_entry():
    harnesses = {
        "gd-full": {
            "target_agent_cli": "codex",
            "components": {
                "instruction_files": ["gd-agents-md"],
                "skills": ["godot-debugging", "visual-qa"],
            }
        }
    }

    resolved = resolve_harness("gd-full", harnesses)

    assert resolved.preset == "gd-full"
    assert resolved.target_agent_cli == "codex"
    assert resolved.components["instruction_files"] == ["gd-agents-md"]
    assert resolved.components["skills"] == ["godot-debugging", "visual-qa"]
    assert resolved.components["hooks"] == []
    assert resolved.fingerprint.startswith("sha256:")


def test_resolve_harness_rejects_inline_component_override():
    presets = {"gd-baseline": {"target_agent_cli": "generic", "components": {}}}

    with pytest.raises(ValueError, match="Harness spec must be a preset name"):
        resolve_harness({"preset": "gd-baseline"}, presets)


def test_fingerprint_changes_when_component_changes():
    base = normalize_components({"skills": ["godot-debugging"]})
    changed = normalize_components({"skills": ["godot-debugging", "visual-qa"]})

    assert fingerprint_components(base) != fingerprint_components(changed)


def test_harness_fingerprint_changes_when_target_agent_changes():
    components = normalize_components({"skills": ["fast-context"]})

    assert fingerprint_harness("codex", components) != fingerprint_harness(
        "claude-code", components
    )


def test_load_experiment_file_expands_test_set_file_cells(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    test_set_file = tmp_path / "testsets.yaml"
    harness_file = tmp_path / "harnesses.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              filtered_30:
                testcases_dir: testcases_filtered
                testcases: [hard-mode-difficulty-preset, collision-layer-precise-edit]
            """
        ),
        encoding="utf-8",
    )
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              gd-baseline:
                target_agent_cli: generic
                components:
                  skills: []
              all-codex:
                target_agent_cli: codex
                components:
                  plugins: [agentic-game-development-superpowers]
                  skills: [fast-context, transcript-viewer]
                  mcp_servers: [node_repl]
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: filtered-harness-ab
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: baseline
                model: gpt-5.4
                agent_cli: codex
                orchestration:
                  id: single-agent
                  source_type: builtin
                task_set: filtered_30
                harness: gd-baseline
              - id: skill
                model: gpt-5.4
                agent_cli: codex
                orchestration:
                  id: single-agent
                  source_type: builtin
                task_set: filtered_30
                harness: all-codex
            """
        )
    )

    experiment = load_experiment_file(experiment_file)

    assert experiment.experiment_id == "filtered-harness-ab"
    assert [cell.cell_id for cell in experiment.cells] == ["baseline", "skill"]
    assert experiment.cells[0].task_set == "filtered_30"
    assert experiment.cells[0].testcases_dir == str(tmp_path / "testcases_filtered")
    assert experiment.cells[0].testcase_ids == [
        "hard-mode-difficulty-preset",
        "collision-layer-precise-edit",
    ]
    assert experiment.cells[1].harness.preset == "all-codex"
    assert experiment.cells[1].harness.target_agent_cli == "codex"
    assert experiment.cells[1].harness.components["plugins"] == [
        "agentic-game-development-superpowers"
    ]
    assert experiment.cells[1].harness.components["skills"] == [
        "fast-context",
        "transcript-viewer",
    ]
    assert experiment.cells[1].harness.components["mcp_servers"] == ["node_repl"]


def test_load_experiment_file_allows_test_set_without_explicit_ids(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    test_set_file = tmp_path / "testsets.yaml"
    harness_file = tmp_path / "harnesses.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              all:
                testcases_dir: testcases
            """
        ),
        encoding="utf-8",
    )
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              gd-baseline:
                target_agent_cli: generic
                components: {}
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: all-cases
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: baseline
                task_set: all
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    experiment = load_experiment_file(experiment_file)

    assert experiment.cells[0].task_set == "all"
    assert experiment.cells[0].testcases_dir == str(tmp_path / "testcases")
    assert experiment.cells[0].testcase_ids == []


def test_load_experiment_file_parses_per_test_timeouts(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    test_set_file = tmp_path / "testsets.yaml"
    harness_file = tmp_path / "harnesses.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              smoke_2:
                testcases_dir: testcases_filtered
                default_timeout: 1200
                testcases:
                  - id: case-a
                    timeout: 12.5
                  - case-b
            """
        ),
        encoding="utf-8",
    )
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              gd-baseline:
                target_agent_cli: generic
                components: {}
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: per-test-timeouts
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: baseline
                task_set: smoke_2
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    experiment = load_experiment_file(experiment_file)

    assert experiment.cells[0].testcase_ids == ["case-a", "case-b"]
    assert experiment.cells[0].testcase_timeouts == {"case-a": 12.5, "case-b": 1200.0}


def test_test_set_file_resolves_testcases_dir_relative_to_itself(tmp_path):
    config_dir = tmp_path / "experiments"
    config_dir.mkdir()
    experiment_file = config_dir / "experiment.yaml"
    test_set_file = config_dir / "testsets.yaml"
    harness_file = config_dir / "harnesses.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              filtered_30:
                testcases_dir: ../testcases_filtered
                testcases: [case-a]
            """
        ),
        encoding="utf-8",
    )
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              gd-baseline:
                target_agent_cli: generic
                components: {}
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: relative-testset-path
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: baseline
                task_set: filtered_30
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    experiment = load_experiment_file(experiment_file)

    assert experiment.cells[0].testcases_dir == str(tmp_path / "testcases_filtered")


def test_cell_testcase_override_wins_over_test_set_file(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    test_set_file = tmp_path / "testsets.yaml"
    harness_file = tmp_path / "harnesses.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              smoke_2:
                testcases_dir: testcases_filtered
                testcases: [case-a, case-b]
            """
        ),
        encoding="utf-8",
    )
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              gd-baseline:
                target_agent_cli: generic
                components: {}
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: override-cases
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: baseline
                task_set: smoke_2
                testcases_dir: custom_cases
                testcases: [case-c]
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    experiment = load_experiment_file(experiment_file)

    assert experiment.cells[0].testcases_dir == "custom_cases"
    assert experiment.cells[0].testcase_ids == ["case-c"]


def test_load_experiment_file_requires_test_set_file(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: missing-testset-file
            cells:
              - id: baseline
                task_set: filtered_30
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="test_set_file is required"):
        load_experiment_file(experiment_file)


def test_load_experiment_file_requires_harness_file(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    test_set_file = tmp_path / "testsets.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              filtered_30:
                testcases_dir: testcases_filtered
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: missing-harness-file
            test_set_file: testsets.yaml
            cells:
              - id: baseline
                task_set: filtered_30
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="harness_file is required"):
        load_experiment_file(experiment_file)


def test_load_experiment_file_rejects_inline_harness_presets(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    test_set_file = tmp_path / "testsets.yaml"
    harness_file = tmp_path / "harnesses.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              filtered_30:
                testcases_dir: testcases_filtered
            """
        ),
        encoding="utf-8",
    )
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              gd-baseline:
                target_agent_cli: generic
                components: {}
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: inline-harness-presets
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            harness_presets:
              gd-skill:
                components:
                  skills: [godot-debugging]
            cells:
              - id: baseline
                task_set: filtered_30
                harness: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="harness_presets is no longer supported"):
        load_experiment_file(experiment_file)


def test_load_experiment_file_rejects_inline_harness_mapping(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    test_set_file = tmp_path / "testsets.yaml"
    harness_file = tmp_path / "harnesses.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              filtered_30:
                testcases_dir: testcases_filtered
            """
        ),
        encoding="utf-8",
    )
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              gd-baseline:
                target_agent_cli: generic
                components: {}
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: inline-harness-map
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: baseline
                task_set: filtered_30
                harness:
                  preset: gd-baseline
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Harness spec must be a preset name"):
        load_experiment_file(experiment_file)


def test_load_experiment_file_rejects_missing_harness_target(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    test_set_file = tmp_path / "testsets.yaml"
    harness_file = tmp_path / "harnesses.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              filtered_30:
                testcases_dir: testcases_filtered
            """
        ),
        encoding="utf-8",
    )
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              all-codex:
                components: {}
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: missing-target
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: codex-cell
                agent_cli: codex
                task_set: filtered_30
                harness: all-codex
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must define target_agent_cli"):
        load_experiment_file(experiment_file)


def test_load_experiment_file_rejects_agent_cli_target_mismatch(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    test_set_file = tmp_path / "testsets.yaml"
    harness_file = tmp_path / "harnesses.yaml"
    test_set_file.write_text(
        textwrap.dedent(
            """
            test_sets:
              filtered_30:
                testcases_dir: testcases_filtered
            """
        ),
        encoding="utf-8",
    )
    harness_file.write_text(
        textwrap.dedent(
            """
            harnesses:
              all-codex:
                target_agent_cli: codex
                components: {}
            """
        ),
        encoding="utf-8",
    )
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: target-mismatch
            test_set_file: testsets.yaml
            harness_file: harnesses.yaml
            cells:
              - id: claude-cell
                agent_cli: claude-code
                task_set: filtered_30
                harness: all-codex
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="targets agent_cli 'codex'"):
        load_experiment_file(experiment_file)


def test_orchestration_support_boundary_is_explicit():
    assert OrchestrationSpec(id="single-agent", source_type="builtin").is_supported
    assert not OrchestrationSpec(
        id="scripted-parallel-verify", source_type="scripted_workflow"
    ).is_supported
