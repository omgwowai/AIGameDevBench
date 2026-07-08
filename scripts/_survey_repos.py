"""Public source URLs for the Godot game repos that AIGameDevCollecter's Survey
tool mined bad-case testcases from.

Used ONLY by the one-off snapshot-making step (scripts/_migrate_survey_to_filtered.py):
the migration clones these, exports the exact baseline commit as an offline
project snapshot under testcases/_snapshots/, and the benchmark runs entirely
offline from that snapshot afterwards. Nothing here is imported at run time.

The survey manifests store a Windows temp path like
    C:\\Users\\...\\Temp\\scout\\godot-open-rpg
so we key on the final path segment (the repo folder name).
"""
from __future__ import annotations

# repo folder name (as it appears in the survey source_repo path) -> public URL
SURVEY_REPO_URLS: dict[str, str] = {
    "godot-open-rpg": "https://github.com/gdquest-demos/godot-open-rpg",
    "godot-open-rts": "https://github.com/lampe-games/godot-open-rts",
    "jdungeon": "https://github.com/jonathaneeckhout/jdungeon",
    "a-little-game-called-mario":
        "https://github.com/a-little-org-called-mario/a-little-game-called-mario",
    "Super-Mario-Bros.-Remastered-Public":
        "https://github.com/JHDev2006/Super-Mario-Bros.-Remastered-Public",
    "tabletop-club": "https://github.com/drwhut/tabletop-club",
}


def repo_name_from_source(source_repo: str) -> str:
    """Extract the repo folder name from a survey source_repo path (Windows or
    POSIX), e.g. 'C:\\...\\scout\\godot-open-rpg' -> 'godot-open-rpg'."""
    s = str(source_repo).replace("\\", "/").rstrip("/")
    return s.rsplit("/", 1)[-1] if "/" in s else s


def resolve_repo_url(source_repo: str) -> str | None:
    return SURVEY_REPO_URLS.get(repo_name_from_source(source_repo))
