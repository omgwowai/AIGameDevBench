#!/usr/bin/env python3
"""Solvability audit for testcases_filtered/.

Gate: a case's golden diff (good.diff / fix.diff) must be reproducible by
editing TEXT files only. Flags:
  - BINARY_PATCH: diff contains "GIT binary patch" (agent cannot recreate assets)
  - NEW_BINARY_FILE: diff adds files with binary extensions
  - NO_GOLDEN: case has neither good.diff nor fix.diff
Exit code 1 if any case is flagged.
"""
import re, sys
from pathlib import Path

BIN_EXT = {".png", ".jpg", ".jpeg", ".ttf", ".otf", ".wav", ".ogg", ".mp3",
           ".webp", ".gif", ".import", ".res", ".scn", ".exe", ".dll"}

def audit(root: Path):
    flagged = []
    for case in sorted(p for p in root.iterdir() if p.is_dir()):
        goldens = [case / "good.diff", case / "fix.diff"]
        goldens = [g for g in goldens if g.exists()]
        if not goldens:
            flagged.append((case.name, "NO_GOLDEN", "no good.diff / fix.diff"))
            continue
        for g in goldens:
            text = g.read_text(encoding="utf-8", errors="replace")
            if "GIT binary patch" in text:
                flagged.append((case.name, "BINARY_PATCH", g.name))
                continue
            for m in re.finditer(r"^diff --git a/(\S+) b/(\S+)", text, re.M):
                ext = Path(m.group(2)).suffix.lower()
                if ext in BIN_EXT and ext != ".import":
                    seg = text[m.start():m.start() + 400]
                    if "new file mode" in seg:
                        flagged.append((case.name, "NEW_BINARY_FILE", f"{g.name}: {m.group(2)}"))
    return flagged

if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "testcases_filtered")
    flagged = audit(root)
    total = sum(1 for p in root.iterdir() if p.is_dir())
    if flagged:
        print(f"FLAGGED {len(set(f[0] for f in flagged))}/{total} case(s):")
        for name, kind, detail in flagged:
            print(f"  [{kind}] {name}  ({detail})")
        sys.exit(1)
    print(f"OK: all {total} cases pass the text-only solvability gate")
