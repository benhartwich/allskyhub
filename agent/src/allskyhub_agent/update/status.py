"""The updater's state for `status` (SPEC §6.3, §8), read by the agent.

The updater runs as root and writes its state file world-readable; the agent only reads it.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from allskyhub_agent.update.installer import read_state
from allskyhub_protocol import UpdateStatus

STATE_FILE = Path("/var/lib/allskyhub-updater/state.json")


def update_status(path: Path = STATE_FILE) -> UpdateStatus | None:
    raw = read_state(path)
    if raw is None:
        return None
    try:
        return UpdateStatus.model_validate(
            {k: raw.get(k) for k in ("state", "version", "at", "code") if raw.get(k) is not None}
        )
    except ValidationError:
        return None
