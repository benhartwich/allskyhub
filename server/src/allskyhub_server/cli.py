"""Command line interface ``allskyhub-server``."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import getpass
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.logconfig import configure_logging
from allskyhub_server.settings import Settings, get_settings

T = TypeVar("T")

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _cmd_dev(args: argparse.Namespace) -> int:
    import uvicorn

    os.environ.setdefault("ALLSKYHUB_SERVER_ENV", "dev")
    # CSRF compares the Origin header with base_url; match what the browser will use.
    os.environ.setdefault("ALLSKYHUB_SERVER_BASE_URL", f"http://{args.host}:{args.port}")
    settings = get_settings()
    configure_logging(settings.log_level, "console")
    uvicorn.run(
        "allskyhub_server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        access_log=False,
        log_config=None,
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Production server behind nginx: systemd socket activation (fd 3) or a Unix socket."""
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    listen: dict[str, object]
    if args.uds:
        listen = {"uds": args.uds}
    elif os.environ.get("LISTEN_FDS"):
        listen = {"fd": 3}
    else:
        print("serve needs --uds PATH or systemd socket activation", file=sys.stderr)
        return 2
    uvicorn.run(
        "allskyhub_server.app:create_app",
        factory=True,
        workers=args.workers,
        access_log=False,
        log_config=None,
        proxy_headers=False,
        server_header=False,
        **listen,  # pyright: ignore[reportArgumentType]
    )
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    settings = get_settings()
    configure_logging(settings.log_level, "console")
    command.upgrade(Config(str(ALEMBIC_INI)), args.revision)
    return 0


def _run_db(work: Callable[[AsyncSession, Settings], Awaitable[T]]) -> T:
    """Run ``work`` with a database session (operator commands)."""
    from allskyhub_server.db import create_engine, create_sessionmaker

    settings = get_settings()
    configure_logging(settings.log_level, "console")

    async def run() -> T:
        engine = create_engine(settings)
        try:
            async with create_sessionmaker(engine)() as db:
                return await work(db, settings)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _read_password(from_stdin: bool) -> str | None:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("Neues Passwort: ")
    if password != getpass.getpass("Wiederholen: "):
        print("Die Passwörter stimmen nicht überein.", file=sys.stderr)
        return None
    return password


def _cmd_invite(args: argparse.Namespace) -> int:
    """Accounts exist only by invitation: prints the link to pass on."""
    from urllib.parse import urlencode

    from allskyhub_server.auth import invitations
    from allskyhub_server.auth.accounts import AccountError

    async def work(db: AsyncSession, settings: Settings) -> int:
        try:
            token = await invitations.create(db, args.email, dt.timedelta(days=args.days))
        except AccountError as exc:
            print(exc.message, file=sys.stderr)
            return 1
        await db.commit()
        url = f"{settings.base_url.rstrip('/')}/invite?{urlencode({'token': token})}"
        print(f"Einladung für {args.email}, {args.days} Tage gültig, nur einmal verwendbar:")
        print(url)
        return 0

    return _run_db(work)


def _cmd_users(args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from allskyhub_server.models import Device, User

    async def work(db: AsyncSession, settings: Settings) -> int:
        rows = await db.execute(
            select(User.email, User.created_at, func.count(Device.id))
            .outerjoin(Device, Device.owner_id == User.id)
            .group_by(User.id)
            .order_by(User.created_at)
        )
        for email, created, cameras in rows:
            print(f"{created:%Y-%m-%d}  {cameras} Kamera(s)  {email}")
        return 0

    return _run_db(work)


def _cmd_set_password(args: argparse.Namespace) -> int:
    """Password reset by the operator; signs the account out everywhere."""
    from sqlalchemy import delete, select

    from allskyhub_server.auth.accounts import MIN_PASSWORD_LENGTH, normalize_email
    from allskyhub_server.auth.passwords import hash_secret_async
    from allskyhub_server.models import AppToken, User, WebSession

    password = _read_password(args.password_stdin)
    if password is None:
        return 1
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Mindestens {MIN_PASSWORD_LENGTH} Zeichen.", file=sys.stderr)
        return 1

    async def work(db: AsyncSession, settings: Settings) -> int:
        user = await db.scalar(select(User).where(User.email == normalize_email(args.email)))
        if user is None:
            print("Kein Konto mit dieser Adresse.", file=sys.stderr)
            return 1
        user.password_hash = await hash_secret_async(password)
        await db.execute(delete(WebSession).where(WebSession.user_id == user.id))
        await db.execute(delete(AppToken).where(AppToken.user_id == user.id))
        await db.commit()
        print(f"Passwort für {user.email} gesetzt; alle Sitzungen beendet.")
        return 0

    return _run_db(work)


def _cmd_delete_user(args: argparse.Namespace) -> int:
    """Delete an account: its cameras are unpaired and their images removed."""
    from sqlalchemy import select

    from allskyhub_server.auth.accounts import delete_account, normalize_email
    from allskyhub_server.devices.images import ImageStore
    from allskyhub_server.models import User

    async def work(db: AsyncSession, settings: Settings) -> int:
        email = normalize_email(args.email)
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            print("Kein Konto mit dieser Adresse.", file=sys.stderr)
            return 1
        if not args.yes:
            print(f"Zum Löschen von {email} zusätzlich --yes angeben.", file=sys.stderr)
            return 2
        device_ids = await delete_account(db, user)
        await db.commit()
        store = ImageStore(settings.image_dir)
        for device_id in device_ids:
            store.delete_device(device_id)
        # Open camera connections end at their next token refresh (at most an hour).
        print(f"{email} gelöscht, {len(device_ids)} Kamera(s) entkoppelt.")
        return 0

    return _run_db(work)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="allskyhub-server")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dev", help="development server on localhost (no nginx)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-reload", dest="reload", action="store_false")
    p.set_defaults(func=_cmd_dev)

    p = sub.add_parser("serve", help="production server (systemd socket or --uds)")
    p.add_argument("--uds", help="Unix socket path (instead of socket activation)")
    p.add_argument("--workers", type=int, default=1)
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("invite", help="invite someone (accounts exist only by invitation)")
    p.add_argument("email")
    p.add_argument("--days", type=int, default=7, help="valid for this many days (default 7)")
    p.set_defaults(func=_cmd_invite)

    sub.add_parser("users", help="list accounts").set_defaults(func=_cmd_users)

    p = sub.add_parser("set-password", help="set a new password (signs out everywhere)")
    p.add_argument("email")
    p.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    p.set_defaults(func=_cmd_set_password)

    p = sub.add_parser("delete-user", help="delete an account and unpair its cameras")
    p.add_argument("email")
    p.add_argument("--yes", action="store_true", help="really delete")
    p.set_defaults(func=_cmd_delete_user)

    p = sub.add_parser("migrate", help="apply database migrations")
    p.add_argument("revision", nargs="?", default="head")
    p.set_defaults(func=_cmd_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
