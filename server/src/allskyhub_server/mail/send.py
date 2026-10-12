"""Sending e-mail through the operator's mail server (SMTP submission with STARTTLS).

Off while ``smtp_host`` is empty: then nothing is sent and pages say so. Plain text only;
the standard library is enough, so no new dependency.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol

from allskyhub_server.settings import Settings

log = logging.getLogger(__name__)
TIMEOUT_S = 20


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def send(self, mail: Mail) -> bool: ...


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.mail_enabled

    def _message(self, mail: Mail) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self._settings.mail_from
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        msg["Date"] = formatdate(localtime=False)
        domain = self._settings.mail_from.rsplit("@", 1)[-1].rstrip(">") or None
        msg["Message-ID"] = make_msgid(domain=domain)
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(mail.body)
        return msg

    def _send_sync(self, mail: Mail) -> None:
        s = self._settings
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=TIMEOUT_S) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(self._message(mail))

    async def send(self, mail: Mail) -> bool:
        """True when the server took the mail; errors are logged, never raised."""
        if not self.enabled:
            return False
        try:
            await asyncio.to_thread(self._send_sync, mail)
        except (OSError, smtplib.SMTPException):
            log.exception("mail could not be sent")
            return False
        return True


class MemoryMailer:
    """For tests and development: keeps the mails instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[Mail] = []

    @property
    def enabled(self) -> bool:
        return True

    async def send(self, mail: Mail) -> bool:
        self.sent.append(mail)
        return True
