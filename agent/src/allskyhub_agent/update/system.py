"""System settings that agent features need beyond the agent's own files (SPEC §8).

An update bundle brings only the agent's venv; the image's system files (groups, units,
`config.txt`) stay as the camera was flashed. Features that need more, like the weather
and lightning sensors on I²C (SPEC §7.11, §7.12), set it up here: the updater (root)
applies these steps on every run by day, so a camera flashed before the feature gets it
with the next update, not only a new image. Each step does nothing when already in place.

Steps that need the agent restarted (new groups) say so; a change in `config.txt` takes
effect only after a reboot, which goes through the daytime reboot for system updates
(`/run/reboot-required`, SPEC §8 System updates).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

Runner = Callable[[list[str]], int]
Output = Callable[[list[str]], str | None]  # a command's stdout, None if it failed

SENSOR_GROUPS = ("i2c", "gpio")
MODULES_LOAD = "etc/modules-load.d/allskyhub-i2c.conf"
CONFIG_TXT = "boot/firmware/config.txt"
I2C_ON = re.compile(r"^\s*dtparam=(?:i2c_arm|i2c)=on\s*(?:#.*)?$", re.MULTILINE)
REBOOT_FLAG = "run/reboot-required"


@dataclass
class Result:
    done: list[str] = field(default_factory=list[str])
    restart: bool = False  # the agent must restart to get new groups
    reboot: bool = False  # config.txt changed


@dataclass
class SystemSync:
    root: Path  # "/" on the camera
    run: Runner
    output: Output
    user: str = "allskyhub"
    service: str = "allskyhub-agent.service"

    def apply(self) -> Result:
        res = Result()
        if not self._has_user():
            return res  # not an allskyhub camera (development machine): leave it alone
        self._groups(res)
        self._unit_groups(res)
        self._i2c_module(res)
        self._config_txt(res)
        if res.reboot:
            self._ask_for_reboot()
        if res.done:
            log.info("system set up: %s", ", ".join(res.done))
        return res

    # --- steps -----------------------------------------------------------------------------

    def _groups(self, res: Result) -> None:
        groups = self._group_members()
        for g in SENSOR_GROUPS:
            if g not in groups and self.run(["groupadd", "--system", g]) == 0:
                res.done.append(f"group {g}")
                groups[g] = set()
        missing = [g for g in SENSOR_GROUPS if g in groups and self.user not in groups[g]]
        if missing and self.run(["usermod", "-aG", ",".join(missing), self.user]) == 0:
            res.done.append(f"{self.user} in {', '.join(missing)}")
            res.restart = True

    def _unit_groups(self, res: Result) -> None:
        """Older images' unit lacks the sensor groups; a drop-in adds them."""
        value = self.output(
            ["systemctl", "show", "--property=SupplementaryGroups", "--value", self.service]
        )
        if value is None or set(SENSOR_GROUPS) <= set(value.split()):
            return
        dropin = self.root / "etc/systemd/system" / f"{self.service}.d" / "10-sensors.conf"
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(
            "# Weather and lightning sensors (SPEC §7.11, §7.12), added by the updater.\n"
            f"[Service]\nSupplementaryGroups={' '.join(SENSOR_GROUPS)}\n",
            encoding="utf-8",
        )
        self.run(["systemctl", "daemon-reload"])
        res.done.append("unit groups")
        res.restart = True

    def _i2c_module(self, res: Result) -> None:
        path = self.root / MODULES_LOAD
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# I²C device files for the weather and lightning sensors (SPEC §7.11, §7.12).\n"
            "i2c-dev\n",
            encoding="utf-8",
        )
        self.run(["modprobe", "i2c-dev"])
        res.done.append("i2c-dev")

    def _config_txt(self, res: Result) -> None:
        """I²C on the header. Already on (raspi-config, the image, an overlay) or no Pi
        boot partition: nothing to do. Appended under `[all]`, so a model section at the
        end of the file can't confine it."""
        path = self.root / CONFIG_TXT
        if not path.is_file() or (self.root / "dev/i2c-1").exists():
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        if I2C_ON.search(text):
            return
        tmp = path.with_name(path.name + ".allskyhub")
        tmp.write_text(
            text
            + ("" if text.endswith("\n") or not text else "\n")
            + "\n# I²C for the weather and lightning sensors (allskyhub, SPEC §7.11).\n"
            "[all]\ndtparam=i2c_arm=on\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        res.done.append("I²C in config.txt")
        res.reboot = True

    def _ask_for_reboot(self) -> None:
        flag = self.root / REBOOT_FLAG
        if flag.parent.is_dir():
            flag.touch()
            pkgs = flag.with_name(flag.name + ".pkgs")
            with pkgs.open("a", encoding="utf-8") as f:
                f.write("allskyhub-i2c\n")

    # --- reading the system -------------------------------------------------------------------

    def _has_user(self) -> bool:
        try:
            lines = (self.root / "etc/passwd").read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        return any(line.split(":", 1)[0] == self.user for line in lines)

    def _group_members(self) -> dict[str, set[str]]:
        try:
            lines = (self.root / "etc/group").read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        out: dict[str, set[str]] = {}
        for line in lines:
            parts = line.split(":")
            if len(parts) >= 4:
                out[parts[0]] = {m for m in parts[3].split(",") if m}
        return out
