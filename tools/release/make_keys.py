#!/usr/bin/env python3
"""Write the update key list (SPEC §8 Key revocation, docs/updates.md).

    make_keys.py SERIAL [--revoke PEM]... [--add PEM]... > keys.json

The list is complete: every key revoked so far goes into each new one. Sign it offline
with the emergency key (keys.json.sig), never in CI.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def _der(pem: Path) -> bytes:
    lines = pem.read_text(encoding="ascii").strip().splitlines()
    if lines[0] != "-----BEGIN PUBLIC KEY-----" or lines[-1] != "-----END PUBLIC KEY-----":
        raise SystemExit(f"{pem}: not a public key (PEM)")
    der = base64.b64decode("".join(lines[1:-1]))
    if len(der) != 44 or not der.startswith(bytes.fromhex("302a300506032b6570032100")):
        raise SystemExit(f"{pem}: not an Ed25519 key")
    return der


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("serial", type=int)
    ap.add_argument("--revoke", type=Path, action="append", default=[])
    ap.add_argument("--add", type=Path, action="append", default=[])
    args = ap.parse_args(argv[1:])
    if args.serial < 1:
        raise SystemExit("serial: 1 or higher, above the list before")
    doc = {
        "serial": args.serial,
        "issued_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "revoked": [hashlib.sha256(_der(p)).hexdigest() for p in args.revoke],
        "added": [base64.b64encode(_der(p)).decode() for p in args.add],
    }
    sys.stdout.write(json.dumps(doc, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
