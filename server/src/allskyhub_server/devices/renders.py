"""Timelapses over a chosen period and keograms over several nights (roadmap C8).

Made on the hub with ffmpeg from what the archive holds: thumbnails about every minute
(kept 30 days) and the nights' keogram thumbnails. One render at a time, at low priority;
results are kept ``keep_render_days`` days.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, Frame, Product, RenderJob
from allskyhub_server.settings import Settings

log = logging.getLogger(__name__)

MAX_SPAN = dt.timedelta(days=31)
MAX_FRAMES = 6000  # longer periods are thinned out evenly
MAX_OPEN_JOBS = 2  # per camera, queued or running
KEOGRAM_STRIP = (48, 360)  # width, height of one night in the keogram over nights
FFMPEG_TIMEOUT_S = 900
SUFFIX = {"timelapse": ".mp4", "keogram": ".jpg"}
MEDIA = {"timelapse": "video/mp4", "keogram": "image/jpeg"}


class RenderError(Exception):
    """A request that cannot be rendered; the message is German, for the page."""


@dataclass(frozen=True)
class Inputs:
    paths: list[Path]


async def _open_jobs(db: AsyncSession, device_id: str) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(RenderJob)
        .where(RenderJob.device_id == device_id, RenderJob.state.in_(("queued", "running")))
    )
    return int(count or 0)


async def request(
    db: AsyncSession,
    device: Device,
    kind: str,
    start: dt.datetime,
    end: dt.datetime,
    fps: int = 25,
) -> RenderJob:
    """Queue a render after checking the request; the caller commits."""
    if kind not in SUFFIX:
        raise RenderError("Unbekannte Art.")
    if end <= start:
        raise RenderError("Das Ende muss nach dem Anfang liegen.")
    if end - start > MAX_SPAN:
        raise RenderError("Höchstens 31 Tage auf einmal.")
    if not 5 <= fps <= 60:
        raise RenderError("Bilder pro Sekunde: 5 bis 60.")
    if await _open_jobs(db, device.id) >= MAX_OPEN_JOBS:
        raise RenderError("Es laufen schon zwei Aufträge für diese Kamera.")
    job = RenderJob(device_id=device.id, kind=kind, start=start, end=end, fps=fps)
    db.add(job)
    await db.flush()
    return job


async def _inputs(db: AsyncSession, store: ImageStore, job: RenderJob) -> Inputs:
    if job.kind == "timelapse":
        frames = list(
            await db.scalars(
                select(Frame)
                .where(
                    Frame.device_id == job.device_id,
                    Frame.has_thumb.is_(True),
                    Frame.captured_at >= job.start,
                    Frame.captured_at <= job.end,
                )
                .order_by(Frame.captured_at)
            )
        )
        if len(frames) > MAX_FRAMES:
            step = len(frames) / MAX_FRAMES
            frames = [frames[int(i * step)] for i in range(MAX_FRAMES)]
        paths = [
            store.frame_path(f.device_id, f.night_id, f.name, FrameVariant.THUMB) for f in frames
        ]
    else:
        first, last = job.start.strftime("%Y%m%d"), job.end.strftime("%Y%m%d")
        nights = list(
            await db.scalars(
                select(Product.night_id)
                .where(
                    Product.device_id == job.device_id,
                    Product.kind == "keogram",
                    Product.has_thumb.is_(True),
                    Product.night_id >= first,
                    Product.night_id <= last,
                )
                .order_by(Product.night_id)
            )
        )
        paths = [
            store.product_path(job.device_id, n, "keogram.jpg", FrameVariant.THUMB) for n in nights
        ]
    return Inputs([p for p in paths if p.is_file()])


def _concat_list(paths: list[Path], fps: int) -> str:
    """ffmpeg concat list: each picture shown 1/fps s (the last one repeated, as the
    demuxer ignores the last duration)."""
    lines = [f"file '{p}'\nduration {1 / fps:.6f}\n" for p in paths]
    return "".join(lines) + f"file '{paths[-1]}'\n"


def _timelapse_args(ffmpeg: str, listing: Path, out: Path, fps: int) -> list[str]:
    return [
        ffmpeg, "-nostdin", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-r", str(fps),
        "-movflags", "+faststart", str(out),
    ]  # fmt: skip


def _keogram_args(ffmpeg: str, paths: list[Path], out: Path) -> list[str]:
    width, height = KEOGRAM_STRIP
    args = [ffmpeg, "-nostdin", "-loglevel", "error", "-y"]
    for p in paths:
        args += ["-i", str(p)]
    scaled = "".join(f"[{i}:v]scale={width}:{height}[s{i}];" for i in range(len(paths)))
    joined = "".join(f"[s{i}]" for i in range(len(paths)))
    n = len(paths)
    stack = f"{joined}hstack=inputs={n}[out]" if n > 1 else f"{joined}null[out]"
    return [*args, "-filter_complex", scaled + stack, "-map", "[out]", "-frames:v", "1",
            "-q:v", "3", str(out)]  # fmt: skip


async def _run(args: list[str]) -> str | None:
    """Run ffmpeg at low priority; returns an error text or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nice", "-n", "15", *args,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
    except FileNotFoundError:
        return "ffmpeg ist auf dem Hub nicht installiert."
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        return "Abgebrochen: hat zu lange gedauert."
    if proc.returncode != 0:
        log.warning("ffmpeg failed", extra={"stderr": stderr.decode(errors="replace")[-500:]})
        return "Das Video konnte nicht erstellt werden."
    return None


async def render(
    maker: async_sessionmaker[AsyncSession],
    store: ImageStore,
    settings: Settings,
    job_id: uuid.UUID,
) -> None:
    async with maker() as db:
        job = await db.get(RenderJob, job_id)
        if job is None:
            return
        inputs = await _inputs(db, store, job)
        kind, fps, device_id = job.kind, job.fps, job.device_id
    out = store.render_path(device_id, str(job_id), SUFFIX[kind])
    error: str | None
    if not inputs.paths:
        error = "Für diesen Zeitraum gibt es keine Bilder (mehr)."
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        if kind == "timelapse":
            with tempfile.NamedTemporaryFile("w", suffix=".txt", dir=out.parent) as listing:
                listing.write(_concat_list(inputs.paths, fps))
                listing.flush()
                args = _timelapse_args(settings.ffmpeg_path, Path(listing.name), out, fps)
                error = await _run(args)
        else:
            error = await _run(_keogram_args(settings.ffmpeg_path, inputs.paths, out))
    async with maker() as db:
        await db.execute(
            update(RenderJob)
            .where(RenderJob.id == job_id)
            .values(
                state="failed" if error else "done",
                error=error,
                frames=len(inputs.paths),
                size=out.stat().st_size if not error and out.is_file() else None,
                finished_at=dt.datetime.now(dt.UTC),
            )
        )
        await db.commit()


class Renderer:
    """Works through the queue, one job at a time; ``wake`` after queueing a job."""

    def __init__(
        self, maker: async_sessionmaker[AsyncSession], store: ImageStore, settings: Settings
    ) -> None:
        self._maker = maker
        self._store = store
        self._settings = settings
        self._wake = asyncio.Event()

    def wake(self) -> None:
        self._wake.set()

    async def run_once(self) -> bool:
        """Render the oldest queued job; False when there is none."""
        async with self._maker() as db:
            job = await db.scalar(
                select(RenderJob)
                .where(RenderJob.state == "queued")
                .order_by(RenderJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return False
            job.state = "running"
            job_id = job.id
            await db.commit()
        try:
            await render(self._maker, self._store, self._settings, job_id)
        except Exception:
            log.exception("render failed")
            async with self._maker() as db:
                await db.execute(
                    update(RenderJob)
                    .where(RenderJob.id == job_id)
                    .values(state="failed", error="Interner Fehler.")
                )
                await db.commit()
        return True

    async def run_forever(self) -> None:
        # Jobs that were running when the hub stopped start again.
        async with self._maker() as db:
            await db.execute(
                update(RenderJob).where(RenderJob.state == "running").values(state="queued")
            )
            await db.commit()
        while True:
            try:
                while await self.run_once():
                    pass
            except Exception:
                log.exception("render queue failed")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), 60)


async def purge(db: AsyncSession, store: ImageStore, settings: Settings, now: dt.datetime) -> None:
    """Remove finished renders after ``keep_render_days``. The caller commits."""
    old = list(
        await db.scalars(
            select(RenderJob).where(
                RenderJob.created_at < now - dt.timedelta(days=settings.keep_render_days)
            )
        )
    )
    for job in old:
        store.render_path(job.device_id, str(job.id), SUFFIX.get(job.kind, "")).unlink(
            missing_ok=True
        )
    if old:
        await db.execute(delete(RenderJob).where(RenderJob.id.in_([j.id for j in old])))
