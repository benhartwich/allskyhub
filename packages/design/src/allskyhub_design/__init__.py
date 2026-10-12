"""The allskyhub look, shared by the hub's web UI and the camera's local UI.

Plain files, no build step: ``tokens.css`` (colours, fonts, radii; the dark theme and the
red night theme), ``base.css`` (sky background, cards, buttons, inputs, navigation), the
Space Grotesk font and the star field. Serve the ``static`` folder under one path, e.g.
``/static/design/``; the CSS refers to the font and the stars relatively.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

FILES = (
    "tokens.css",
    "base.css",
    "fonts/space-grotesk.woff2",
    "stars.svg",
    "stars-bright.svg",
)


def static_dir() -> Path:
    """The folder with all files (an installed wheel unpacks it on disk)."""
    return Path(str(files("allskyhub_design") / "static"))


def path(name: str) -> Path:
    """One of ``FILES``; ``KeyError`` for anything else."""
    if name not in FILES:
        raise KeyError(name)
    return static_dir() / name
