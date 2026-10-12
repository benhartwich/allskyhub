"""What the telescope next to the camera does, read from N.I.N.A. (SPEC §7.5, roadmap C15).

N.I.N.A.'s Advanced API plugin (christian-photo/ninaAPI, v2, default port 1888) has no
authentication; it is reachable on the local network only. Every 30 s the agent reads the
image history (the newest light frame: target, filter, exposure, HFR, stars) and the
sequence (how many instructions are done), and reports it as `status.observatory`. It only
reads; it never controls N.I.N.A. When N.I.N.A. does not answer, `connected` is false and
the last known state stays.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from typing import Any, cast

import httpx
from pydantic import ValidationError

from allskyhub_protocol import Observatory, ObservatoryImage, ObservatorySequence

log = logging.getLogger(__name__)

POLL_S = 30.0
_DONE = {"FINISHED", "SKIPPED"}


def _response(r: httpx.Response) -> object:
    if r.status_code != 200:
        return None
    data: object = r.json()
    if not isinstance(data, dict):
        return None
    return cast("dict[str, Any]", data).get("Response")


def _when(raw: object, tz: tzinfo) -> datetime | None:
    """N.I.N.A. writes local time, sometimes without an offset."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return at.replace(tzinfo=tz) if at.tzinfo is None else at


def _num(h: dict[str, Any], key: str) -> float | None:
    v = h.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
        return None
    return float(v)


def last_light(history: object, tz: tzinfo) -> tuple[str | None, ObservatoryImage | None]:
    """The target and facts of the newest light frame in `/image-history?all=true`."""
    if not isinstance(history, list):
        return None, None
    for item in reversed(cast("list[Any]", history)):
        if not isinstance(item, dict):
            continue
        h = cast("dict[str, Any]", item)
        if str(h.get("ImageType", "LIGHT")).upper() != "LIGHT":
            continue

        stars = _num(h, "Stars")
        target = str(h.get("TargetName") or "")[:200] or None
        image = ObservatoryImage(
            at=_when(h.get("Date"), tz),
            filter=str(h.get("Filter") or "")[:40] or None,
            exposure_s=_num(h, "ExposureTime"),
            hfr=round(hfr, 2) if (hfr := _num(h, "HFR")) is not None else None,
            stars=int(stars) if stars is not None else None,
        )
        return target, image
    return None, None


def sequence_progress(sequence: object) -> ObservatorySequence | None:
    """From `/sequence/json`: the running container's name and the share of finished
    instructions; None without a loaded sequence."""
    if not isinstance(sequence, list):
        return None
    leaves: list[str] = []
    running: list[str] = []

    def walk(node: object, depth: int) -> None:
        if not isinstance(node, dict):
            return
        n = cast("dict[str, Any]", node)
        items: object = n.get("Items")
        status = str(n.get("Status", "")).upper()
        if isinstance(items, list):
            if status == "RUNNING" and depth > 0 and isinstance(n.get("Name"), str):
                running.append(n["Name"])
            for child in cast("list[Any]", items):
                walk(child, depth + 1)
        elif "Status" in n:
            leaves.append(status)

    for top in cast("list[Any]", sequence):
        walk(top, 0)
    if not leaves:
        return None
    done = sum(1 for s in leaves if s in _DONE)
    name = running[-1][:200] if running else None
    return ObservatorySequence(name=name, progress=round(done / len(leaves), 3))


class NinaRelay:
    def __init__(
        self,
        url: str,
        tz: tzinfo,
        safe: Callable[[], bool | None],
        client: Callable[[], httpx.Client] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        base = url.rstrip("/")
        if not base.endswith("/v2/api"):
            base += "/v2/api"
        self._client = client or (lambda: httpx.Client(base_url=base, timeout=10.0))
        self._tz = tz
        self._safe = safe
        self._now = now
        self._lock = threading.Lock()
        self._state: Observatory | None = None
        self._stop = threading.Event()

    @property
    def latest(self) -> Observatory | None:
        """For status (SPEC §6.3), with the safety monitor's current answer."""
        with self._lock:
            state = self._state
        return state.model_copy(update={"safe": self._safe()}) if state is not None else None

    def poll(self) -> None:
        now = self._now()
        try:
            with self._client() as http:
                history = _response(http.get("/image-history", params={"all": "true"}))
                sequence = _response(http.get("/sequence/json"))
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("N.I.N.A. not reachable: %s", exc)
            with self._lock:
                old = self._state
                self._state = (
                    old.model_copy(update={"connected": False, "at": now})
                    if old is not None
                    else Observatory(at=now, connected=False)
                )
            return
        try:
            target, image = last_light(history, self._tz)
            state = Observatory(
                at=now,
                connected=True,
                target=target,
                sequence=sequence_progress(sequence),
                last_image=image,
            )
        except ValidationError as exc:
            log.warning("unexpected answer from N.I.N.A.: %d errors", exc.error_count())
            return
        with self._lock:
            self._state = state

    def run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(POLL_S)

    def start(self) -> None:
        threading.Thread(target=self.run, name="nina", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
