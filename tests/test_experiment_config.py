import textwrap

import pytest

from aigamedevbench.experiment_config import (
    COMPONENT_KEYS,
    OrchestrationSpec,
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
        {
            "preset": "gd-full",
            "add": {"skills": ["visual-qa"]},
            "remove": {"hooks": ["session-start-context"]},
        },
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


def test_load_experiment_file_expands_filtered_cells(tmp_path):
    experiment_file = tmp_path / "experiment.yaml"
    experiment_file.write_text(
        textwrap.dedent(
            """
            id: filtered-harness-ab
            task_sets:
              filtered30:
                testcases_dir: testcases_filtered
                testcases: [hard-mode-difficulty-preset, collision-layer-precise-edit]
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
                task_set: filtered30
                harness:
                  preset: gd-baseline
              - id: skill
                model: gpt-5.4
                agent_cli: codex
                orchestration:
                  id: single-agent
                  source_type: builtin
                task_set: filtered30
                harness:
                  preset: gd-skill
            """
        )
    )

    experiment = load_experiment_file(experiment_file)

    assert experiment.experiment_id == "filtered-harness-ab"
    assert [cell.cell_id for cell in experiment.cells] == ["baseline", "skill"]
    assert experiment.cells[0].task_set == "filtered30"
    assert experiment.cells[0].testcases_dir == "testcases_filtered"
    assert experiment.cells[0].testcase_ids == [
        "hard-mode-difficulty-preset",
        "collision-layer-precise-edit",
    ]
    assert experiment.cells[1].harness.components["skills"] == ["godot-debugging"]


def test_orchestration_support_boundary_is_explicit():
    assert OrchestrationSpec(id="single-agent", source_type="builtin").is_supported
    assert not OrchestrationSpec(
        id="scripted-parallel-verify", source_type="scripted_workflow"
    ).is_supported
