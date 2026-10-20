#!/usr/bin/env python3
"""Print the GitHub release notes for an agent version from its news entry (SPEC §8 What's
new), or fail when it has none: every release says what it brings.

    release_notes.py VERSION > notes.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

NEWS = Path(__file__).resolve().parents[2] / "agent/src/allskyhub_agent/update/news.json"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[1]
    entry = next((e for e in json.loads(NEWS.read_text("utf-8")) if e["version"] == version), None)
    if entry is None:
        print(f"::error::no entry for {version} in {NEWS.name}", file=sys.stderr)
        return 1
    lines = [
        "## Neu",
        *(f"- {p}" for p in entry["de"]),
        "",
        "## New",
        *(f"- {p}" for p in entry["en"]),
    ]
    lines += [
        "",
        f"Agent update {version} (docs/updates.md). New cameras: write "
        f"allskyhub-{version}.img.xz to a microSD card (docs/install.md).",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
