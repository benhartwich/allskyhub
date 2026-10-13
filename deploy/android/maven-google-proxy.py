"""Google's Maven repository for the Android test build (deploy/android/README.md).

From this server dl.google.com answers HTTP/2 only; Gradle speaks HTTP/1.1 and times out.
This proxy serves the repository over plain HTTP on a local address and fetches over
HTTP/2 (curl, at most ``PARALLEL`` at a time). Artifacts are immutable and kept on disk;
``maven-metadata.xml`` is always fetched. A file with a ``.sha1`` next to it upstream is
kept only if it matches, so a broken download never reaches Gradle. Standard library only:

    python3 maven-google-proxy.py HOST PORT CACHE
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sys
from pathlib import Path

UPSTREAM = "https://dl.google.com/dl/android/maven2/"
SAFE = re.compile(r"^[A-Za-z0-9._/+-]+$")
PARALLEL = 8
CHECKSUMS = (".sha1", ".md5", ".sha256", ".sha512", ".asc")


async def curl(url: str, *out: str) -> tuple[bool, bytes]:
    proc = await asyncio.create_subprocess_exec(
        "curl", "-sfL", "--http2", "--max-time", "3600", *out, url,
        stdout=asyncio.subprocess.PIPE,
    )  # fmt: skip
    body, _ = await proc.communicate()
    return proc.returncode == 0, body


async def main(host: str, port: int, cache: Path) -> None:
    slots = asyncio.Semaphore(PARALLEL)
    busy: dict[str, asyncio.Lock] = {}  # one download per file; Gradle asks in parallel

    async def fetch(path: str, local: Path) -> bool:
        """Into ``local``; False for a missing file or a failure."""
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(local.name + ".part")
        async with slots:
            ok, _ = await curl(UPSTREAM + path, "-o", str(tmp))
            if ok and not path.endswith(CHECKSUMS):
                found, sha1 = await curl(UPSTREAM + path + ".sha1")
                expected = sha1.decode("ascii", "replace").split()[0:1]
                if found and expected:
                    digest = hashlib.sha1(tmp.read_bytes(), usedforsecurity=False).hexdigest()
                    ok = digest == expected[0].lower()  # integrity, not trust: TLS is that
        if ok:
            tmp.replace(local)
        else:
            tmp.unlink(missing_ok=True)
        return ok

    async def answer(writer: asyncio.StreamWriter, status: int, body: bytes, head: bool) -> None:
        reason = {200: "OK", 404: "Not Found", 400: "Bad Request"}[status]
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode()
        )
        if not head:
            writer.write(body)
        await writer.drain()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await reader.readline()).decode("latin-1").split()
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass
            if len(line) < 2 or line[0] not in ("GET", "HEAD"):
                await answer(writer, 400, b"", False)
                return
            method, path = line[0], line[1].split("?")[0].lstrip("/")
            head = method == "HEAD"
            if not SAFE.match(path) or ".." in path:
                await answer(writer, 400, b"", head)
                return
            local = cache / path
            async with busy.setdefault(path, asyncio.Lock()):
                fresh = local.is_file() and not path.endswith("maven-metadata.xml")
                if not fresh and not await fetch(path, local):
                    await answer(writer, 404, b"", head)
                    return
                body = local.read_bytes()
            await answer(writer, 200, body, head)
        finally:
            writer.close()

    server = await asyncio.start_server(handle, host, port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])))
