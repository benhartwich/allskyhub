#!/usr/bin/env python3
"""Write the update channel manifest for an agent bundle (SPEC §8, docs/updates.md).

    make_manifest.py BUNDLE VERSION URL > manifest.json

The CI signs the manifest's bytes afterwards (Ed25519, detached, manifest.json.sig).
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    bundle, version, url = Path(argv[1]), argv[2], argv[3]
    digest = hashlib.sha256()
    with bundle.open("rb") as fh:
        while block := fh.read(1 << 20):
            digest.update(block)
    manifest = {
        "channel": "stable",
        "version": version,
        "released_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "bundle": {"url": url, "sha256": digest.hexdigest(), "size": bundle.stat().st_size},
    }
    sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
