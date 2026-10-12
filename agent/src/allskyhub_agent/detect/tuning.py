"""How readily each detector reports (roadmap L8): "low", "normal" or "high", or "off".

"normal" is each detector's tuned default. The other levels move only the one or two
thresholds that decide between a faint real event and a false alarm; everything else
(twinkle, aircraft, cloud tests) stays as tuned.
"""

from __future__ import annotations

from dataclasses import replace

from allskyhub_agent.detect.aurora import AuroraConfig
from allskyhub_agent.detect.lightning import LightningConfig
from allskyhub_agent.detect.meteor import MeteorConfig
from allskyhub_agent.detect.nlc import NlcConfig
from allskyhub_agent.settings import AgentSettings

# low: only clear events; high: fainter ones too, with more false alarms.
_METEOR = {"low": (28, 70.0), "high": (17, 40.0)}  # diff_threshold, min_length_px
_LIGHTNING = {"low": 0.01, "high": 0.0025}  # min_area_frac
_AURORA = {"low": 1.2, "high": 0.3}  # min_index
_NLC = {"low": 2.0, "high": 0.5}  # min_index


def meteor_config(level: str) -> MeteorConfig:
    cfg = MeteorConfig()
    if level in _METEOR:
        diff, length = _METEOR[level]
        cfg = replace(cfg, diff_threshold=diff, min_length_px=length)
    return cfg


def lightning_config(level: str) -> LightningConfig:
    cfg = LightningConfig()
    return replace(cfg, min_area_frac=_LIGHTNING[level]) if level in _LIGHTNING else cfg


def aurora_config(level: str) -> AuroraConfig:
    cfg = AuroraConfig()
    return replace(cfg, min_index=_AURORA[level]) if level in _AURORA else cfg


def nlc_config(level: str) -> NlcConfig:
    cfg = NlcConfig()
    return replace(cfg, min_index=_NLC[level]) if level in _NLC else cfg


def enabled(s: AgentSettings) -> dict[str, bool]:
    return {
        "meteor": s.detect_meteor != "off",
        "aurora": s.detect_aurora != "off",
        "lightning": s.detect_lightning != "off",
        "nlc": s.detect_nlc != "off",
    }
