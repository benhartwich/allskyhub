"""Settings the agent changes itself (hub URL from setup), kept across restarts."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

DEFAULT_PATH = Path("/var/lib/allskyhub-agent/settings.json")
DEFAULT_HUB = "https://allskyhub.org"


@dataclass(frozen=True)
class AgentSettings:
    hub_url: str = DEFAULT_HUB

    @classmethod
    def load(cls, path: Path) -> AgentSettings:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        hub = cast("dict[str, object]", data).get("hub_url")
        return cls(hub_url=hub if isinstance(hub, str) and hub else DEFAULT_HUB)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(asdict(self), f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
