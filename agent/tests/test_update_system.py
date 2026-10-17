"""System steps the updater applies for new features (SPEC §8 System steps)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from allskyhub_agent.update.system import SystemSync

OLD_CONFIG = "# Pi config\ndtparam=audio=on\n[cm5]\ndtoverlay=dwc2\n"


@dataclass
class Pi:
    """A camera flashed before the sensors: no sensor groups for the agent, I²C off."""

    root: Path
    unit_groups: str = "video"
    commands: list[list[str]] = field(default_factory=list[list[str]])

    def __post_init__(self) -> None:
        etc = self.root / "etc"
        etc.mkdir(parents=True)
        (etc / "passwd").write_text("root:x:0:0::/root:/bin/bash\nallskyhub:x:990:990::/:/x\n")
        (etc / "group").write_text("video:x:44:allskyhub\ni2c:x:998:\nallskyhub:x:990:\n")
        boot = self.root / "boot/firmware"
        boot.mkdir(parents=True)
        (boot / "config.txt").write_text(OLD_CONFIG)
        (self.root / "run").mkdir()

    def run(self, cmd: list[str]) -> int:
        self.commands.append(cmd)
        group = self.root / "etc/group"
        if cmd[:2] == ["groupadd", "--system"]:
            group.write_text(group.read_text() + f"{cmd[2]}:x:997:\n")
        if cmd[:2] == ["usermod", "-aG"]:
            lines: list[str] = []
            for line in group.read_text().splitlines():
                name, x, gid, members = line.split(":")
                if name in cmd[2].split(","):
                    members = ",".join([m for m in members.split(",") if m] + [cmd[3]])
                lines.append(f"{name}:{x}:{gid}:{members}")
            group.write_text("\n".join(lines) + "\n")
        return 0

    def output(self, cmd: list[str]) -> str | None:
        if cmd[0] == "systemctl" and "--property=SupplementaryGroups" in cmd:
            dropin = self.root / "etc/systemd/system/allskyhub-agent.service.d/10-sensors.conf"
            return self.unit_groups + (" i2c gpio" if dropin.exists() else "")
        return None

    def sync(self) -> SystemSync:
        return SystemSync(self.root, self.run, self.output)


def test_an_old_camera_gets_the_sensor_setup(tmp_path: Path) -> None:
    pi = Pi(tmp_path)
    res = pi.sync().apply()
    assert ["groupadd", "--system", "gpio"] in pi.commands
    assert ["usermod", "-aG", "i2c,gpio", "allskyhub"] in pi.commands
    assert ["systemctl", "daemon-reload"] in pi.commands
    assert ["modprobe", "i2c-dev"] in pi.commands
    dropin = tmp_path / "etc/systemd/system/allskyhub-agent.service.d/10-sensors.conf"
    assert "SupplementaryGroups=i2c gpio" in dropin.read_text()
    assert (tmp_path / "etc/modules-load.d/allskyhub-i2c.conf").read_text().endswith("i2c-dev\n")
    config = (tmp_path / "boot/firmware/config.txt").read_text()
    # Kept as it was, then I²C for every model: not inside the [cm5] section.
    assert config.startswith(OLD_CONFIG)
    assert config.endswith("[all]\ndtparam=i2c_arm=on\n")
    assert res.restart
    assert res.reboot
    assert (tmp_path / "run/reboot-required").exists()
    assert "allskyhub-i2c" in (tmp_path / "run/reboot-required.pkgs").read_text()


def test_a_second_run_changes_nothing(tmp_path: Path) -> None:
    pi = Pi(tmp_path)
    pi.sync().apply()
    config = (tmp_path / "boot/firmware/config.txt").read_text()
    pi.commands.clear()
    res = pi.sync().apply()
    assert res.done == []
    assert not res.restart
    assert not res.reboot
    assert pi.commands == []
    assert (tmp_path / "boot/firmware/config.txt").read_text() == config


def test_a_current_image_needs_nothing(tmp_path: Path) -> None:
    pi = Pi(tmp_path, unit_groups="video i2c gpio")
    (tmp_path / "etc/group").write_text(
        "video:x:44:allskyhub\ni2c:x:998:allskyhub\ngpio:x:997:allskyhub\n"
    )
    (tmp_path / "boot/firmware/config.txt").write_text(OLD_CONFIG + "[all]\ndtparam=i2c_arm=on\n")
    (tmp_path / "etc/modules-load.d").mkdir()
    (tmp_path / "etc/modules-load.d/allskyhub-i2c.conf").write_text("i2c-dev\n")
    res = pi.sync().apply()
    assert res.done == []
    assert pi.commands == []


def test_i2c_already_on_another_way_is_left_alone(tmp_path: Path) -> None:
    pi = Pi(tmp_path)
    (tmp_path / "dev").mkdir()
    (tmp_path / "dev/i2c-1").touch()  # raspi-config or an overlay switched it on
    res = pi.sync().apply()
    assert (tmp_path / "boot/firmware/config.txt").read_text() == OLD_CONFIG
    assert not res.reboot


def test_not_a_camera_is_left_alone(tmp_path: Path) -> None:
    pi = Pi(tmp_path)
    (tmp_path / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\n")
    res = pi.sync().apply()
    assert res.done == []
    assert pi.commands == []
