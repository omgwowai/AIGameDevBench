from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from aigamedevbench.godot_bin import resolve_godot_binary

# Path prefixes whose files are NOT the case's subject and must not gate it.
# Third-party plugins under addons/ ship editor-only scenes/scripts (they wire
# signals to @tool editor methods and load editor singletons) that legitimately
# error under a headless runtime boot — noise unrelated to any testcase's bug.
# Overridable via config global.validation.excluded_path_prefixes.
DEFAULT_EXCLUDED_PREFIXES = ("addons/",)


def _norm(rel: str) -> str:
    return str(rel).replace("\\", "/")


def _is_excluded(rel: str, excluded_prefixes: tuple[str, ...]) -> bool:
    r = _norm(rel)
    return any(r.startswith(p) for p in excluded_prefixes)


def _is_explicit_binary(binary: str) -> bool:
    """A path-like binary (has a separator or drive colon) was deliberately
    specified; a bare name like 'godot' is the default-on-PATH lookup. An
    unresolvable explicit binary is a misconfiguration to report, whereas a
    missing bare 'godot' just means godot isn't installed (skip silently)."""
    return any(sep in binary for sep in ("/", "\\", ":"))


@dataclass
class VerificationResult:
    l0_pass: bool = True
    l1_pass: bool = True
    l0_details: list[str] = field(default_factory=list)
    l1_details: list[str] = field(default_factory=list)
    # Per-stage wall time in milliseconds. Populated by run_validation so the
    # report can show where the (often godot-import-dominated) time goes, not
    # just the harness's wall_time. Keys: import_ms, l0_ms, l1_ms, validation_ms.
    timings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "l0_pass": self.l0_pass,
            "l1_pass": self.l1_pass,
            "l0_details": self.l0_details,
            "l1_details": self.l1_details,
            "timings": self.timings,
        }


# --- Godot import cache ---

# File extensions whose (re)import Godot must perform: textures, audio, fonts,
# meshes, and the .import sidecars that describe them. Editing a .gd/.tscn/.tres
# does NOT need a reimport pass — those are read directly at scene load.
_IMPORTABLE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".svg", ".exr", ".hdr",
    ".ogg", ".wav", ".mp3",
    ".ttf", ".otf", ".woff", ".woff2",
    ".obj", ".glb", ".gltf", ".fbx", ".dae",
    ".import",
}


def _has_importable_changes(changed_files: list[str] | None) -> bool:
    """Whether the harness touched any file that requires a Godot (re)import.

    Conservative: with changed_files unknown (None) we assume yes, so we never
    skip an import that might have been needed. A changed .gd/.tscn/.tres never
    triggers a reimport; a new/changed .png/.ogg/.import etc. does."""
    if changed_files is None:
        return True
    for rel in changed_files:
        dot = rel.rfind(".")
        if dot != -1 and rel[dot:].lower() in _IMPORTABLE_EXTS:
            return True
    return False


def godot_import(project_root: Path, godot_binary: str = "godot",
                 timeout: int = 120,
                 changed_files: list[str] | None = None) -> tuple[str | None, bool]:
    """Build the Godot import cache (.godot/imported/*) for the workspace.

    Folder-type baselines carry a .godot/ cache (regenerated locally, gitignored)
    that folder_workspace copies into the workspace. When that cache is already
    present AND the harness changed no importable asset, the ~2.5s `--import`
    pass (Godot boot + filesystem scan, dominated by fixed startup cost, not
    asset work) is redundant: the scene loads directly from the copied cache. We
    skip it in that case — a large win under --repeat, which pays it N times.

    A missing .godot/ (cold worktree, git-type case), a changed asset, or unknown
    changes all force the real import pass, so correctness is never traded away.

    Returns (error, skipped): error is None on success or a short diagnostic
    string on failure (a failure leaves the cache incomplete, which later makes a
    verifier's load() return null and hang until timeout — surfaced here up front
    instead of as a misleading "godot timed out"). skipped is True when the
    import pass was safely elided.
    """
    resolved = resolve_godot_binary(godot_binary)
    if resolved is None:
        if _is_explicit_binary(godot_binary):
            return f"godot binary '{godot_binary}' not found (check --godot-binary)", False
        return None, False  # no godot: not an import failure; downstream skips godot too
    if not (project_root / "project.godot").exists():
        return None, False  # not a Godot project (e.g. a py_config data repo): nothing to import
    # Skip the redundant pass when a cache is already present and nothing that
    # needs (re)import changed.
    if (project_root / ".godot").is_dir() and not _has_importable_changes(changed_files):
        return None, True
    try:
        proc = subprocess.run(
            [resolved, "--headless", "--path", str(project_root), "--import"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"godot --import timed out after {timeout}s (import cache may be incomplete)", False
    except FileNotFoundError:
        return None, False
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return f"godot --import exited {proc.returncode}: {tail[0]}", False
    return None, False


# --- L0: GDScript syntax + headless scene load ---

def _count_paren_depth(content: str) -> int | None:
    """Net paren depth, ignoring parens inside string literals and # comments.

    Returns the remaining open-paren count (0 = balanced, >0 = unclosed), or
    None if a ')' ever closes below zero (unmatched ')'). GDScript strings use
    ' or " (no f-strings); a backslash escapes the next char inside a string.
    Counting raw characters would false-positive on code like
    `name.split("(")[0]`, so string/comment regions are skipped.
    """
    depth = 0
    quote: str | None = None
    escaped = False
    for char in content:
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\" and quote != "\n":
                escaped = True  # backslash escapes only inside string literals
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "#":
            # Comment runs to end of line: treat newline as its closing "quote".
            quote = "\n"
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
    return depth


def check_gd_syntax(project_root: Path, gd_files: list[str]) -> list[str]:
    issues = []
    for gd_rel in gd_files:
        gd_path = project_root / gd_rel
        if not gd_path.exists():
            issues.append(f"{gd_rel}: file not found")
            continue
        content = gd_path.read_text(encoding="utf-8", errors="replace")
        paren_depth = _count_paren_depth(content)
        if paren_depth is None:
            issues.append(f"{gd_rel}: unmatched ')'")
        elif paren_depth > 0:
            issues.append(f"{gd_rel}: unclosed '(' ({paren_depth} remaining)")

        func_no_colon = re.compile(r"^func\s+\w+\s*\([^)]*\)\s*$", re.MULTILINE)
        for match in func_no_colon.finditer(content):
            line_num = content[:match.start()].count("\n") + 1
            issues.append(f"{gd_rel}:{line_num}: function definition missing ':'")
    return issues


def run_l0(project_root: Path, scenes: list[str], godot_binary: str = "godot",
           excluded_prefixes: tuple[str, ...] = DEFAULT_EXCLUDED_PREFIXES) -> tuple[bool, list[str]]:
    issues: list[str] = []
    # Third-party plugin scripts (addons/) are not the case's subject; skip them
    # so their editor-only code can't fail the syntax gate.
    gd_files = [str(p.relative_to(project_root)) for p in project_root.rglob("*.gd")
                if not _is_excluded(p.relative_to(project_root), excluded_prefixes)]
    issues.extend(check_gd_syntax(project_root, gd_files))

    resolved = resolve_godot_binary(godot_binary)
    if resolved is None and _is_explicit_binary(godot_binary):
        issues.append(f"godot binary '{godot_binary}' not found (check --godot-binary)")
    elif resolved:
        for scene in scenes:
            try:
                result = subprocess.run(
                    [resolved, "--headless", "--path", str(project_root), scene, "--quit-after", "2"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=10,
                )
                if result.returncode != 0:
                    issues.append(f"L0 crash: {scene} exited with code {result.returncode}")
                for line in (result.stderr or "").splitlines():
                    if "ERROR" in line:
                        issues.append(f"L0 crash: {scene}: {line.strip()}")
            except subprocess.TimeoutExpired:
                issues.append(f"L0 crash: {scene} timed out after 10s")
            except FileNotFoundError:
                issues.append(f"L0 crash: godot binary '{godot_binary}' not found")
                break
    return len(issues) == 0, issues


# --- L1: scene resource + signal target integrity ---

def check_script_references(project_root: Path, tscn_files: list[str]) -> list[str]:
    issues = []
    ext_resource_re = re.compile(r'\[ext_resource\s.*?path="res://([^"]+)"')
    for tscn_rel in tscn_files:
        tscn_path = project_root / tscn_rel
        if not tscn_path.exists():
            continue
        content = tscn_path.read_text(encoding="utf-8", errors="replace")
        for match in ext_resource_re.finditer(content):
            ref_path = match.group(1)
            if not (project_root / ref_path).exists():
                issues.append(f"{tscn_rel}: missing resource '{ref_path}'")
    return issues


_FUNC_RE = re.compile(r"^func\s+(\w+)\s*\(", re.MULTILINE)
_EXT_SCRIPT_RE = re.compile(r'\[ext_resource\s.*?type="Script"\s.*?path="res://([^"]+)"')
_EXT_PACKED_SCENE_RE = re.compile(
    r'\[ext_resource\s.*?type="PackedScene"\s.*?path="res://([^"]+)"')


def _scene_methods(project_root: Path, scene_rel: str,
                   _seen: set[str] | None = None) -> set[str]:
    """All method names reachable from a scene: methods on scripts the scene
    references directly, plus (recursively) the root scripts of any PackedScene
    it instances. A signal connection in main.tscn can target a method on an
    instanced sub-scene's node (e.g. PlayerBody), whose script is not a direct
    ext_resource of main.tscn — so following PackedScene instances is required
    to avoid false 'method not defined' positives."""
    _seen = _seen if _seen is not None else set()
    if scene_rel in _seen:
        return set()
    _seen.add(scene_rel)
    scene_path = project_root / scene_rel
    if not scene_path.exists():
        return set()
    content = scene_path.read_text(encoding="utf-8", errors="replace")
    methods: set[str] = set()
    for sp in _EXT_SCRIPT_RE.findall(content):
        script_full = project_root / sp
        if script_full.exists():
            methods.update(_FUNC_RE.findall(
                script_full.read_text(encoding="utf-8", errors="replace")))
    for sub in _EXT_PACKED_SCENE_RE.findall(content):
        methods.update(_scene_methods(project_root, sub, _seen))
    return methods


def check_signal_targets(project_root: Path, tscn_files: list[str]) -> list[str]:
    issues = []
    connection_re = re.compile(
        r'\[connection\s+signal="([^"]+)"\s+from="([^"]+)"\s+to="([^"]+)"\s+method="([^"]+)"\]'
    )
    for tscn_rel in tscn_files:
        tscn_path = project_root / tscn_rel
        if not tscn_path.exists():
            continue
        content = tscn_path.read_text(encoding="utf-8", errors="replace")
        defined_methods = _scene_methods(project_root, tscn_rel)
        for match in connection_re.finditer(content):
            signal_name = match.group(1)
            method_name = match.group(4)
            if method_name not in defined_methods:
                issues.append(
                    f"{tscn_rel}: signal '{signal_name}' connected to method '{method_name}' which is not defined"
                )
    return issues


def run_l1(project_root: Path, changed_files: list[str],
           excluded_prefixes: tuple[str, ...] = DEFAULT_EXCLUDED_PREFIXES) -> tuple[bool, list[str]]:
    tscn_files = [f for f in changed_files
                  if f.endswith(".tscn") and not _is_excluded(f, excluded_prefixes)]
    if not tscn_files:
        # Fallback: scan the whole project, but never gate on third-party plugin
        # scenes (addons/) — their editor scenes wire signals to @tool methods
        # absent at runtime, which is noise unrelated to the case under test.
        tscn_files = [str(p.relative_to(project_root)) for p in project_root.rglob("*.tscn")
                      if not _is_excluded(p.relative_to(project_root), excluded_prefixes)]
    if not tscn_files:
        return True, []
    issues: list[str] = []
    issues.extend(check_script_references(project_root, tscn_files))
    issues.extend(check_signal_targets(project_root, tscn_files))
    return len(issues) == 0, issues


def run_validation(repo_root: Path, changed_files: list[str], config: dict) -> VerificationResult:
    godot_binary = config.get("global", {}).get("godot", {}).get("binary", "godot")
    # Path prefixes excluded from L0/L1 gating (default: third-party addons/).
    # Configurable via global.validation.excluded_path_prefixes.
    validation_cfg = config.get("global", {}).get("validation", {})
    excluded_prefixes = tuple(validation_cfg.get("excluded_path_prefixes",
                                                 DEFAULT_EXCLUDED_PREFIXES))
    stage_start = time.perf_counter()
    # Build the import cache once before any headless boot (L0 here, and the
    # runtime verifier later) so scenes with imported resources can load.
    # godot_import is typically the most expensive gate step (~2.5s of Godot
    # startup), so time it separately AND skip it when safe (cache present + no
    # asset change) — the report attributes both the cost and the skip.
    import_error, import_skipped = godot_import(repo_root, godot_binary,
                                                changed_files=changed_files)
    import_ms = (time.perf_counter() - stage_start) * 1000.0

    stage_start = time.perf_counter()
    scenes = [f for f in changed_files
              if f.endswith(".tscn") and not _is_excluded(f, excluded_prefixes)]
    l0_pass, l0_details = run_l0(repo_root, scenes, godot_binary, excluded_prefixes)
    l0_ms = (time.perf_counter() - stage_start) * 1000.0
    if import_error is not None:
        # An incomplete import cache is the upstream cause of later "load() == null"
        # verifier hangs; record it on L0 (and fail the gate) so it is visible.
        l0_details = [f"import: {import_error}", *l0_details]
        l0_pass = False

    stage_start = time.perf_counter()
    l1_pass, l1_details = run_l1(repo_root, changed_files, excluded_prefixes)
    l1_ms = (time.perf_counter() - stage_start) * 1000.0

    return VerificationResult(
        l0_pass=l0_pass, l1_pass=l1_pass,
        l0_details=l0_details, l1_details=l1_details,
        timings={
            "import_ms": round(import_ms, 1),
            "import_skipped": import_skipped,
            "l0_ms": round(l0_ms, 1),
            "l1_ms": round(l1_ms, 1),
            "validation_ms": round(import_ms + l0_ms + l1_ms, 1),
        },
    )
