"""Offline conformance + smoke tests for .beaver/tasks/aigdbench/.

IMPORTANT (see docs/beaverhub-taskpackage.md "Known gaps"): this module does
NOT run BeaverHub's real `beaver task conformance` Go validator -- this
sandbox has no BeaverHub Go toolchain and no cluster access. The
``_validate_task_package`` / ``_MiniSchemaValidator`` helpers below are a
same-rules Python re-implementation of the checks in BeaverHub's
``internal/taskspec/taskpackage_validate.go`` (as read from that file at
authoring time), applied directly to this repo's own task.yaml/schemas. They
are a defense-in-depth shape check, not a substitute for the real validator.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = REPO_ROOT / ".beaver" / "tasks" / "aigdbench"

# --- regexes re-derived from BeaverHub internal/taskspec/{validate,taskpackage_validate}.go ---
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_BINARY_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CAPABILITY_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CREDENTIAL_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DURATION_RE = re.compile(r"^(\d+(\.\d+)?(ns|us|\xb5s|ms|s|m|h))+$")


def _is_repo_rel_path(p: str) -> bool:
    if not p or p.startswith("/"):
        return False
    return not any(seg == ".." for seg in p.split("/"))


def _has_parent_segment(p: str) -> bool:
    return any(seg == ".." for seg in p.split("/"))


def _is_generic_capability(cap: str) -> bool:
    if not cap:
        return False
    return all(_CAPABILITY_SEGMENT_RE.match(seg) for seg in cap.split("."))


def _validate_task_package(doc: dict) -> None:
    """Raise AssertionError with a clear message on the first rule violated."""
    assert doc.get("apiVersion") == "beaverhub.dev/task/v1", "unsupported apiVersion"
    assert doc.get("kind") == "TaskPackage", "unsupported kind"

    meta = doc.get("metadata") or {}
    assert _NAME_RE.match(str(meta.get("name", ""))), "metadata.name is not a stable lowercase slug"
    version = meta.get("version", "")
    assert version and not re.search(r"\s", version), "metadata.version must be non-empty, no whitespace"

    spec = doc.get("spec") or {}

    runtime = ((spec.get("runtime") or {}).get("requires")) or {}
    for key, constraint in (runtime.get("binaries") or {}).items():
        assert _BINARY_KEY_RE.match(key), f"binaries key {key!r} is not a lowercase slug"
        assert str(constraint).strip(), f"binaries[{key!r}] has an empty version constraint"
    for cap in runtime.get("capabilities") or []:
        assert not cap.startswith("supports_"), f"capability {cap!r} is business-named (supports_*)"
        assert _is_generic_capability(cap), f"capability {cap!r} is not a generic dotted slug"

    argv = ((spec.get("entrypoint") or {}).get("argv")) or []
    assert argv, "entrypoint.argv must be non-empty"
    assert all(isinstance(a, str) and a for a in argv), "entrypoint.argv must have no empty element"

    schema_ref = ((spec.get("input") or {}).get("schema")) or ""
    if schema_ref:
        assert _is_repo_rel_path(schema_ref), "input.schema must be repo-relative"
        assert (REPO_ROOT / schema_ref).is_file(), f"input.schema {schema_ref!r} does not exist"

    output = spec.get("output") or {}
    result_path = output.get("result", "")
    artifacts_path = output.get("artifacts", "")
    assert result_path or artifacts_path, "output must declare result or artifacts"
    for label, p in (("output.result", result_path), ("output.artifacts", artifacts_path)):
        if not p:
            continue
        assert not _has_parent_segment(p), f"{label} must not contain a '..' segment"
        assert p == "/out" or p.startswith("/out/"), f"{label} must sit under /out"

    creds = spec.get("credentials") or []
    assert isinstance(creds, list), "credentials must be a list"
    for c in creds:
        assert _CREDENTIAL_NAME_RE.match(c), f"credential {c!r} is not an env-var-style NAME"

    timeout = spec.get("timeout", "")
    assert timeout and _DURATION_RE.match(timeout), f"timeout {timeout!r} is not a positive Go-duration-shaped string"


def test_task_yaml_parses_and_is_valid_yaml() -> None:
    doc = yaml.safe_load((TASK_DIR / "task.yaml").read_text(encoding="utf-8"))
    assert isinstance(doc, dict)


def test_task_yaml_satisfies_taskpackage_v1_rules() -> None:
    doc = yaml.safe_load((TASK_DIR / "task.yaml").read_text(encoding="utf-8"))
    _validate_task_package(doc)


def test_task_yaml_declares_the_shipped_entrypoint_and_schemas() -> None:
    doc = yaml.safe_load((TASK_DIR / "task.yaml").read_text(encoding="utf-8"))
    spec = doc["spec"]
    argv = spec["entrypoint"]["argv"]
    entry_path = REPO_ROOT / argv[-1]
    assert entry_path.is_file(), f"declared entrypoint {argv[-1]!r} must exist"
    assert (TASK_DIR / "input.schema.json").is_file()
    assert (TASK_DIR / "result.schema.json").is_file()


def test_task_yaml_declares_no_network_egress_or_credentials() -> None:
    # Documented, deliberate choice (docs/beaverhub-taskpackage.md "Known
    # gaps"): this TaskPackage stays offline/no-secret. Pin it so a future
    # edit can't silently widen the capability surface.
    doc = yaml.safe_load((TASK_DIR / "task.yaml").read_text(encoding="utf-8"))
    spec = doc["spec"]
    caps = spec["runtime"]["requires"].get("capabilities") or []
    assert "network.egress" not in caps
    assert spec.get("credentials") in (None, [])


# --- minimal, dependency-free JSON-Schema-shaped validator -----------------
#
# NOT a general JSON Schema implementation: supports exactly the keywords our
# two schemas use (type, properties, required, additionalProperties, enum,
# pattern, minimum, maximum, items), applied to a plain-object instance. Good
# enough to prove our schemas actually accept the fixtures this repo's docs
# claim they accept, and reject what they claim they reject -- see the module
# docstring for why this isn't a substitute for a real validator.
class _SchemaViolation(AssertionError):
    pass


def _mini_validate(instance, schema) -> None:
    if "enum" in schema:
        if instance not in schema["enum"]:
            raise _SchemaViolation(f"{instance!r} not in enum {schema['enum']!r}")
        return
    t = schema.get("type")
    if t == "object":
        if not isinstance(instance, dict):
            raise _SchemaViolation(f"{instance!r} is not an object")
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in instance:
                raise _SchemaViolation(f"missing required property {req!r}")
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(props)
            if extra:
                raise _SchemaViolation(f"unknown propert{'y' if len(extra) == 1 else 'ies'}: {sorted(extra)}")
        for key, value in instance.items():
            if key in props:
                _mini_validate(value, props[key])
    elif t == "array":
        if not isinstance(instance, list):
            raise _SchemaViolation(f"{instance!r} is not an array")
        item_schema = schema.get("items")
        if item_schema:
            for item in instance:
                _mini_validate(item, item_schema)
    elif t == "string":
        if not isinstance(instance, str):
            raise _SchemaViolation(f"{instance!r} is not a string")
        pattern = schema.get("pattern")
        if pattern and not re.match(pattern, instance):
            raise _SchemaViolation(f"{instance!r} does not match pattern {pattern!r}")
    elif t == "boolean":
        if not isinstance(instance, bool):
            raise _SchemaViolation(f"{instance!r} is not a boolean")
    elif t == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise _SchemaViolation(f"{instance!r} is not an integer")
    elif isinstance(t, list):
        # e.g. ["number", "null"] on mean_score
        ok = False
        for sub in t:
            try:
                _mini_validate(instance, {**schema, "type": sub})
                ok = True
                break
            except _SchemaViolation:
                continue
        if not ok:
            raise _SchemaViolation(f"{instance!r} matches none of {t!r}")
    elif t == "null":
        if instance is not None:
            raise _SchemaViolation(f"{instance!r} is not null")
    elif t == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            raise _SchemaViolation(f"{instance!r} is not a number")
        if "minimum" in schema and instance < schema["minimum"]:
            raise _SchemaViolation(f"{instance!r} < minimum {schema['minimum']!r}")
        if "maximum" in schema and instance > schema["maximum"]:
            raise _SchemaViolation(f"{instance!r} > maximum {schema['maximum']!r}")
    # schema with no "type" (e.g. a bare {"enum": [...]}) already handled above.


def _load_schema(name: str) -> dict:
    return json.loads((TASK_DIR / name).read_text(encoding="utf-8"))


def test_input_schema_is_valid_json_and_object_shaped() -> None:
    schema = _load_schema("input.schema.json")
    assert schema["type"] == "object"
    assert schema["required"] == ["driver"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "instance",
    [
        {"driver": "noop"},
        {"driver": "patch"},
        {"driver": "patch", "testcase": "collision-layer-precise-edit"},
    ],
)
def test_input_schema_accepts_valid_fixtures(instance) -> None:
    _mini_validate(instance, _load_schema("input.schema.json"))


@pytest.mark.parametrize(
    "instance",
    [
        {},  # missing required 'driver'
        {"driver": "yolo"},  # not in enum
        {"driver": "noop", "bogus": 1},  # additionalProperties: false
        {"driver": "noop", "testcase": "../etc"},  # violates pattern
        {"driver": "noop", "testcase": ".hidden"},  # violates pattern (leading dot)
    ],
)
def test_input_schema_rejects_invalid_fixtures(instance) -> None:
    with pytest.raises(_SchemaViolation):
        _mini_validate(instance, _load_schema("input.schema.json"))


def test_result_schema_is_valid_json_and_object_shaped() -> None:
    schema = _load_schema("result.schema.json")
    assert schema["type"] == "object"
    assert set(schema["required"]) == {
        "testcase", "driver", "status", "exit_code", "mean_score", "stdout_path", "stderr_path",
    }
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "instance",
    [
        {
            "testcase": "collision-layer-precise-edit",
            "driver": "noop",
            "status": "success",
            "exit_code": 0,
            "mean_score": 0.0,
            "checks": [{"name": "intended_change", "passed": False, "detail": "..."}],
            "stdout_path": "/out/aigdbench.stdout",
            "stderr_path": "/out/aigdbench.stderr",
        },
        {
            "testcase": "collision-layer-precise-edit",
            "driver": "patch",
            "status": "success",
            "exit_code": 0,
            "mean_score": 1.0,
            "stdout_path": "/out/aigdbench.stdout",
            "stderr_path": "/out/aigdbench.stderr",
        },
        {
            # a CONFIG_ERROR result-writer path: mean_score may be null
            "testcase": "collision-layer-precise-edit",
            "driver": "noop",
            "status": "failed",
            "exit_code": 1,
            "mean_score": None,
            "stdout_path": "/out/aigdbench.stdout",
            "stderr_path": "/out/aigdbench.stderr",
        },
    ],
)
def test_result_schema_accepts_valid_fixtures(instance) -> None:
    _mini_validate(instance, _load_schema("result.schema.json"))


@pytest.mark.parametrize(
    "instance",
    [
        {"driver": "noop", "status": "success", "exit_code": 0, "mean_score": 0.0,
         "stdout_path": "/out/a", "stderr_path": "/out/b"},  # missing 'testcase'
        {"testcase": "x", "driver": "weird", "status": "success", "exit_code": 0,
         "mean_score": 0.0, "stdout_path": "/out/a", "stderr_path": "/out/b"},  # bad driver enum
        {"testcase": "x", "driver": "noop", "status": "success", "exit_code": 0,
         "mean_score": 2.0, "stdout_path": "/out/a", "stderr_path": "/out/b"},  # out of range
    ],
)
def test_result_schema_rejects_invalid_fixtures(instance) -> None:
    with pytest.raises(_SchemaViolation):
        _mini_validate(instance, _load_schema("result.schema.json"))


# --- offline consumer smoke: run.sh's own CONFIG_ERROR paths, no Godot needed ---

RUN_SH = TASK_DIR / "run.sh"


def _run(tmp_path: Path, input_body, *, testcase: str | None = None) -> subprocess.CompletedProcess:
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = in_dir / "input.json"
    if isinstance(input_body, str):
        input_path.write_text(input_body, encoding="utf-8")
    else:
        input_path.write_text(json.dumps(input_body), encoding="utf-8")
    env = {
        "BEAVER_INPUT": str(input_path),
        "BEAVER_OUT": str(out_dir),
        "PATH": _os_path(),
    }
    return subprocess.run(
        ["bash", str(RUN_SH)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _os_path() -> str:
    import os
    return os.environ.get("PATH", "")


@pytest.mark.skipif(
    shutil.which("bash") is None or not RUN_SH.exists(),
    reason="needs a bash on PATH to run run.sh",
)
class TestRunShConfigErrorPaths:
    """None of these need a Godot binary or the aigamedevbench package: every
    case here is rejected by run.sh's own input-validation layer before it
    ever tries to resolve or invoke the `aigdbench` CLI."""

    def test_missing_input_file(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        proc = subprocess.run(
            ["bash", str(RUN_SH)],
            cwd=REPO_ROOT,
            env={"BEAVER_INPUT": str(tmp_path / "does-not-exist.json"),
                 "BEAVER_OUT": str(out_dir), "PATH": _os_path()},
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 2, proc.stderr
        assert "not readable" in proc.stderr

    def test_malformed_json(self, tmp_path: Path) -> None:
        proc = _run(tmp_path, "not json")
        assert proc.returncode == 2, proc.stderr
        assert "not valid JSON" in proc.stderr

    def test_missing_driver(self, tmp_path: Path) -> None:
        proc = _run(tmp_path, {"testcase": "collision-layer-precise-edit"})
        assert proc.returncode == 2, proc.stderr
        assert "driver" in proc.stderr

    def test_bad_driver_enum(self, tmp_path: Path) -> None:
        proc = _run(tmp_path, {"driver": "yolo"})
        assert proc.returncode == 2, proc.stderr
        assert "noop, patch" in proc.stderr

    def test_unknown_input_key(self, tmp_path: Path) -> None:
        proc = _run(tmp_path, {"driver": "noop", "bogus": 1})
        assert proc.returncode == 2, proc.stderr
        assert "unknown key" in proc.stderr

    def test_path_traversal_testcase_id(self, tmp_path: Path) -> None:
        proc = _run(tmp_path, {"driver": "noop", "testcase": "../../etc"})
        assert proc.returncode == 2, proc.stderr
        assert "bare testcase id" in proc.stderr

    def test_unknown_testcase(self, tmp_path: Path) -> None:
        proc = _run(tmp_path, {"driver": "noop", "testcase": "does-not-exist-anywhere"})
        assert proc.returncode == 2, proc.stderr
        assert "unknown testcase" in proc.stderr


def test_no_console_script_fallback_actually_invokes_main() -> None:
    """Regression test for the run.sh fallback bug: `python3 -m
    aigamedevbench.cli` only imports the module and exits 0 -- cli.py's
    main() is a click group with no `if __name__ == "__main__"` guard, so
    -m never actually invokes it. run.sh's no-console-script branch now
    calls `python3 -c "from aigamedevbench.cli import main; main()"`
    instead; assert that form really does reach and run main(), and that
    the old -m form really is the silent no-op it was fixed because of
    (so this test fails loudly, not silently, if either regresses)."""
    import os

    env = dict(os.environ)
    src_dir = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = (
        f"{src_dir}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else src_dir
    )

    fixed = subprocess.run(
        [sys.executable, "-c", "from aigamedevbench.cli import main; main()", "--help"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert fixed.returncode == 0, fixed.stderr
    assert "AIGameDevBench" in fixed.stdout, (
        f"main() did not appear to run: stdout={fixed.stdout!r} stderr={fixed.stderr!r}"
    )

    broken = subprocess.run(
        [sys.executable, "-m", "aigamedevbench.cli", "--help"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert broken.returncode == 0
    assert broken.stdout == "", (
        "cli.py grew a __main__ guard (or -m now behaves differently) -- if "
        "`python3 -m aigamedevbench.cli` actually runs main() now, run.sh's "
        "fallback can go back to the simpler -m form and this assertion "
        "should be updated"
    )


# --- optional real end-to-end smoke, only when a Godot binary is supplied ---
#
# A real headless Godot engine is NOT assumed to be present wherever `pytest`
# runs (see docs/beaverhub-taskpackage.md "Verification"). Point
# AIGDBENCH_TEST_GODOT_BINARY at one (this repo's own dev evidence used a
# local Godot 4.6.2 binary) to actually exercise the noop=0.00/patch=1.00
# contract end to end; otherwise this is skipped with a clear reason, not
# silently passed.
def test_real_noop_and_patch_scoring_when_godot_available(tmp_path: Path) -> None:
    import os
    import shutil

    godot_bin = os.environ.get("AIGDBENCH_TEST_GODOT_BINARY") or shutil.which("godot")
    if not godot_bin:
        pytest.skip(
            "no Godot binary available (set AIGDBENCH_TEST_GODOT_BINARY to a "
            "headless Godot >=4.6 executable to run this real end-to-end check)"
        )
    if not shutil.which("aigdbench"):
        pytest.skip("the 'aigdbench' console script is not installed on PATH")

    def run_driver(driver: str, testcase: str | None = None):
        in_dir = tmp_path / driver / "in"
        out_dir = tmp_path / driver / "out"
        in_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)
        body = {"driver": driver}
        if testcase:
            body["testcase"] = testcase
        (in_dir / "input.json").write_text(json.dumps(body), encoding="utf-8")
        import os as _os
        env = dict(_os.environ)
        env["BEAVER_INPUT"] = str(in_dir / "input.json")
        env["BEAVER_OUT"] = str(out_dir)
        env["AIGDBENCH_GODOT_BINARY"] = godot_bin
        proc = subprocess.run(
            ["bash", str(RUN_SH)], cwd=REPO_ROOT, env=env,
            capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
        _mini_validate(result, _load_schema("result.schema.json"))
        return result

    noop = run_driver("noop")
    assert noop["status"] == "success"
    assert noop["mean_score"] == 0.0

    patched = run_driver("patch")
    assert patched["status"] == "success"
    assert patched["mean_score"] == 1.0
