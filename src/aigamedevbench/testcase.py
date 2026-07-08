from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

CATEGORIES = {
    "behavior_logic",
    "intent_translation",
    "precise_edit",
    "architecture",
    "visual_audio",
}

VERIFIER_TYPES = {
    "godot_scenetree",
    "godot_scene_assert",
    "py_config",
    "py_tscn_diff",
    "py_gdscript_ast",
    "visual_static",
    "interaction_routing",
    "survey_bad_case",
}

SOURCE_KINDS = {"git", "folder"}


@dataclass
class Testcase:
    id: str
    category: str
    baseline_ref: str
    task: str
    verifier_type: str
    verifier_entry: str
    scoring_mode: str
    dir: Path
    source_kind: str = "git"
    source_repo: str | None = None
    # Optional: name of a shared project snapshot under <testcases-dir>/_snapshots/.
    # When set on a folder-type case, its baseline comes from that shared snapshot
    # (checked out once from a public repo, offline afterwards) instead of a
    # per-case baseline/ dir. Lets many git-derived bug-fix cases reuse one
    # project tree without vendoring a full copy each.
    snapshot: str | None = None
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "baseline_ref": self.baseline_ref,
            "task": self.task,
            "verifier_type": self.verifier_type,
            "verifier_entry": self.verifier_entry,
            "scoring_mode": self.scoring_mode,
            "source_kind": self.source_kind,
            "source_repo": self.source_repo,
            "snapshot": self.snapshot,
            "provenance": self.provenance,
        }


def load_testcase(dir: Path) -> Testcase:
    manifest = dir / "testcase.toml"
    if not manifest.exists():
        raise FileNotFoundError(f"no testcase.toml in {dir}")
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    tc = data["testcase"]
    verifier = data["verifier"]
    scoring = data.get("scoring", {})
    category = tc["category"]
    if category not in CATEGORIES:
        raise ValueError(f"invalid category '{category}' (allowed: {sorted(CATEGORIES)})")
    vtype = verifier["type"]
    if vtype not in VERIFIER_TYPES:
        raise ValueError(f"invalid verifier type '{vtype}'")
    source_kind = tc.get("source_kind", "git")
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"invalid source_kind '{source_kind}' (allowed: {sorted(SOURCE_KINDS)})")
    # git-type testcases check out a commit; folder-type carry a baseline/ dir instead.
    baseline_ref = tc.get("baseline_ref", "") if source_kind == "folder" else tc["baseline_ref"]
    return Testcase(
        id=tc["id"],
        category=category,
        baseline_ref=baseline_ref,
        task=tc["task"],
        verifier_type=vtype,
        verifier_entry=verifier["entry"],
        scoring_mode=scoring.get("mode", "checkpoints"),
        dir=dir,
        source_kind=source_kind,
        source_repo=tc.get("source_repo"),
        snapshot=tc.get("snapshot"),
        provenance=data.get("provenance", {}),
    )


def discover_testcases(root: Path) -> list[Testcase]:
    out: list[Testcase] = []
    for child in sorted(root.iterdir()):
        if child.name.startswith("_"):
            continue
        if child.is_dir() and (child / "testcase.toml").exists():
            out.append(load_testcase(child))
    return out
