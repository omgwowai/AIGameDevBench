from __future__ import annotations

import pytest

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


def test_load_execution_config_default_path_reads_checked_in_config():
    assert load_execution_config() == ExecutionConfig(
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

    with pytest.raises(ValueError, match="experiment_jobs must be >= 1"):
        load_execution_config(path)


def test_load_execution_config_allows_global_limit_below_parallelism_product(tmp_path):
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
