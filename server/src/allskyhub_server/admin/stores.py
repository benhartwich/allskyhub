"""App store accounts and app releases from the admin page (Benjamin, 2026-10-15).

Benjamin uploads the Google Play service account, an App Store Connect API key and a
Codemagic API token on the admin page. The hub keeps them in ``<data>/store`` (folder
0700, files 0600, never in the database, never shown again) and makes the two signing
keys itself: the Android upload keystore and the private key of the iOS distribution
certificate.

"Einrichten" checks each account, registers the bundle id with Apple, adds the public
repository to Codemagic and puts the build secrets into the app's secure variable group
``allskyhub_store`` there. The release workflows in codemagic.yaml read them from that
group; Codemagic's own signing setup (keystore upload, App Store Connect integration) is
not needed. Android release builds run on the hub's own server instead
(deploy/android/allskyhub-android-build.sh --release, started through ``<data>/android-release``
like a deploy, answers in ``RELEASE_OUT``); the root helper hands the App Bundle back to the hub
(``allskyhub-server play-upload``), which sends it to Play's internal test track. Nothing
here prints or renders a secret: pages and the CLI only see the
non-secret facts kept next to each one (service account address, key id, fingerprints).
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import os
import re
import secrets as _random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from allskyhub_server.devices.images import jpeg_size

log = logging.getLogger(__name__)

PACKAGE = "org.allskyhub.app"  # Android application id and iOS bundle id
APP_NAME = "allskyhub"
GROUP = "allskyhub_store"  # Codemagic variable group, read by codemagic.yaml
WORKFLOWS = {"ios": "ios-release"}  # Android builds on the hub's server (deploy/android)
PLATFORMS = ("android", "ios")
KEY_ALIAS = "upload"
MAX_UPLOAD = 64 * 1024
TIMEOUT = 20.0
UPLOAD_TIMEOUT = 600.0  # an App Bundle is some 50 MB
TRACK = "internal"
RELEASE_OUT = Path("/var/lib/allskyhub-android/release")  # deploy/android, root-owned
# The store listing: texts and graphics in the repository (app/store/play), phone
# screenshots uploaded on the admin page.
LISTING = Path(__file__).resolve().parents[4] / "app" / "store" / "play"
LANGUAGES = ("de-DE", "en-US")  # the first is the default language
CONTACT_EMAIL = "support@webinx.eu"  # as in the imprint
CONTACT_WEBSITE = "https://allskyhub.org"
SCREENSHOTS = (2, 8)
SCREENSHOT_MAX = 8 * 1024 * 1024
LOG_TAIL = 60

CODEMAGIC = "https://codemagic.io/api/v3"
CODEMAGIC_V1 = "https://api.codemagic.io"
ASC = "https://api.appstoreconnect.apple.com/v1"
PLAY = "https://androidpublisher.googleapis.com/androidpublisher/v3"
PLAY_UPLOAD = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3"
PLAY_SCOPE = "https://www.googleapis.com/auth/androidpublisher"

# What Benjamin uploads, then what the hub makes itself.
TITLES = {
    "play": "Google-Play-Dienstkonto",
    "asc": "App-Store-Connect-API-Schlüssel",
    "codemagic": "Codemagic-API-Token",
    "android_upload": "Android-Upload-Schlüssel",
    "ios_certificate": "Schlüssel des iOS-Zertifikats",
}
UPLOADED = ("play", "asc", "codemagic")
# Variables an earlier version put into the Codemagic group, for the Android builds there.
OBSOLETE = frozenset(
    {
        "GCLOUD_SERVICE_ACCOUNT_CREDENTIALS",
        "ALLSKYHUB_UPLOAD_KEYSTORE",
        "ALLSKYHUB_KEYSTORE_PASSWORD",
        "ALLSKYHUB_KEY_PASSWORD",
        "ALLSKYHUB_KEY_ALIAS",
    }
)

KEY_ID = re.compile(r"^[A-Z0-9]{10}$")
ISSUER_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{16,512}$")


class StoreError(Exception):
    """German message for the page; never contains a secret."""


# --- The vault -------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    kind: str
    title: str
    meta: dict[str, Any] | None  # None: not there yet


class Vault:
    """One secret per kind in ``<kind>.secret``, its non-secret facts in ``<kind>.json``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _write(self, name: str, data: bytes) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.root / f".{name}.{_random.token_hex(4)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        tmp.replace(self.root / name)

    def put(self, kind: str, secret: bytes, meta: dict[str, Any], by: str) -> None:
        if kind not in TITLES:
            raise ValueError(kind)
        self._write(f"{kind}.secret", secret)
        self._write(f"{kind}.json", json.dumps({**meta, "at": _now(), "by": by}).encode())
        log.info("store secret %s saved by %s", kind, by)

    def update_meta(self, kind: str, **values: Any) -> None:
        meta = self.meta(kind)
        if meta is not None:
            self._write(f"{kind}.json", json.dumps({**meta, **values}).encode())

    def meta(self, kind: str) -> dict[str, Any] | None:
        try:
            raw = json.loads((self.root / f"{kind}.json").read_text())
        except (OSError, ValueError):
            return None
        if not (self.root / f"{kind}.secret").exists() or not isinstance(raw, dict):
            return None
        return cast(dict[str, Any], raw)

    def has(self, kind: str) -> bool:
        return self.meta(kind) is not None

    def secret(self, kind: str, purpose: str) -> bytes:
        log.info("store secret %s used: %s", kind, purpose)
        return (self.root / f"{kind}.secret").read_bytes()

    def entries(self) -> list[Entry]:
        return [Entry(kind, title, self.meta(kind)) for kind, title in TITLES.items()]

    def builds(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads((self.root / "builds.json").read_text())
        except (OSError, ValueError):
            return []
        return cast(list[dict[str, Any]], raw) if isinstance(raw, list) else []

    def add_build(self, build: dict[str, Any]) -> None:
        self._write("builds.json", json.dumps([build, *self.builds()][:10]).encode())


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


# --- What Benjamin uploads -------------------------------------------------------------


def parse_play(raw: bytes) -> tuple[bytes, dict[str, Any]]:
    """A Google Cloud service account key (JSON)."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise StoreError("Das ist keine JSON-Datei.") from None
    if not isinstance(data, dict):
        raise StoreError("Das ist kein Dienstkonto-Schlüssel.")
    key = cast(dict[str, Any], data)
    if key.get("type") != "service_account" or not all(
        isinstance(key.get(k), str) and key[k]
        for k in ("client_email", "private_key", "token_uri", "project_id")
    ):
        raise StoreError(
            "Das ist kein Dienstkonto-Schlüssel (type service_account mit private_key)."
        )
    try:
        _rsa_key(key["private_key"].encode())
    except StoreError:
        raise StoreError("Der private Schlüssel in der Datei lässt sich nicht lesen.") from None
    return raw, {"client_email": key["client_email"], "project_id": key["project_id"]}


def parse_asc(p8: bytes, key_id: str, issuer_id: str) -> tuple[bytes, dict[str, Any]]:
    """An App Store Connect team API key: the .p8 file, its key id and the issuer id."""
    key_id, issuer_id = key_id.strip().upper(), issuer_id.strip().lower()
    if not KEY_ID.match(key_id):
        raise StoreError("Die Key-ID hat zehn Zeichen, z. B. 2X9R4HXF34.")
    if not ISSUER_ID.match(issuer_id):
        raise StoreError("Die Issuer-ID ist eine UUID, sie steht über der Schlüsselliste.")
    try:
        key = serialization.load_pem_private_key(p8.strip(), password=None)
    except (ValueError, TypeError):
        raise StoreError("Die .p8-Datei lässt sich nicht lesen.") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise StoreError("Die .p8-Datei enthält keinen App-Store-Connect-Schlüssel.")
    return p8.strip() + b"\n", {"key_id": key_id, "issuer_id": issuer_id}


def parse_codemagic(token: str) -> tuple[bytes, dict[str, Any]]:
    token = token.strip()
    if not TOKEN.match(token):
        raise StoreError("Das sieht nicht wie ein Codemagic-API-Token aus.")
    return token.encode(), {}


# --- What the hub makes itself ---------------------------------------------------------


def make_android_upload() -> tuple[bytes, dict[str, Any]]:
    """The upload key for Play App Signing: RSA 4096 in a PKCS#12 keystore, alias
    ``upload``, valid for 30 years (Play wants validity past 2033)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "allskyhub upload"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "allskyhub"),
        ]
    )
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30 * 365))
        .sign(key, hashes.SHA256())
    )
    password = _random.token_urlsafe(24)
    p12 = pkcs12.serialize_key_and_certificates(
        KEY_ALIAS.encode(),
        key,
        cert,
        None,
        serialization.BestAvailableEncryption(password.encode()),
    )
    secret = json.dumps({"p12": base64.b64encode(p12).decode(), "password": password})
    return secret.encode(), {
        "sha256": _fingerprint(cert, hashes.SHA256()),
        "sha1": _fingerprint(cert, hashes.SHA1()),  # noqa: S303 - Google still asks for it
        "valid_until": cert.not_valid_after_utc.date().isoformat(),
    }


def _fingerprint(cert: x509.Certificate, algorithm: hashes.HashAlgorithm) -> str:
    return ":".join(f"{b:02X}" for b in cert.fingerprint(algorithm))


def make_ios_certificate_key() -> tuple[bytes, dict[str, Any]]:
    """The private key Codemagic's ``fetch-signing-files --create`` makes the distribution
    certificate with (RSA 2048, PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return pem, {}


# --- JSON web tokens -------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _signing_input(header: dict[str, Any], claims: dict[str, Any]) -> bytes:
    parts = (json.dumps(x, separators=(",", ":")).encode() for x in (header, claims))
    return ".".join(_b64url(p) for p in parts).encode()


def _rsa_key(pem: bytes) -> rsa.RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError):
        raise StoreError("Schlüssel nicht lesbar.") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise StoreError("Kein RSA-Schlüssel.")
    return key


def jwt_rs256(pem: bytes, claims: dict[str, Any]) -> str:
    signing = _signing_input({"alg": "RS256", "typ": "JWT"}, claims)
    signature = _rsa_key(pem).sign(signing, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing.decode()}.{_b64url(signature)}"


def jwt_es256(pem: bytes, key_id: str, claims: dict[str, Any]) -> str:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise StoreError("Kein App-Store-Connect-Schlüssel.")
    signing = _signing_input({"alg": "ES256", "kid": key_id, "typ": "JWT"}, claims)
    r, s = decode_dss_signature(key.sign(signing, ec.ECDSA(hashes.SHA256())))
    return f"{signing.decode()}.{_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# --- Results ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    title: str
    state: str  # ok, warn, fail
    text: str


@dataclass
class Report:
    steps: list[Step] = field(default_factory=lambda: list[Step]())

    def add(self, title: str, state: str, text: str) -> None:
        self.steps.append(Step(title, state, text))

    @property
    def ok(self) -> bool:
        return all(s.state != "fail" for s in self.steps)


def _message(r: httpx.Response) -> str:
    """The service's own error text (never an echo of what we sent)."""
    try:
        body: Any = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    if isinstance(body, dict):
        b = cast(dict[str, Any], body)
        err: Any = b.get("error")
        if isinstance(err, dict):
            text = cast(dict[str, Any], err).get("message")
            if isinstance(text, str):
                return f"HTTP {r.status_code}: {text[:300]}"
        errors: Any = b.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            first = cast(dict[str, Any], errors[0])
            text = first.get("detail") or first.get("title")
            if isinstance(text, str):
                return f"HTTP {r.status_code}: {text[:300]}"
        for k in ("message", "detail", "error_description"):
            if isinstance(b.get(k), str):
                return f"HTTP {r.status_code}: {str(b[k])[:300]}"
    return f"HTTP {r.status_code}"


def _dict(body: Any) -> dict[str, Any]:
    return cast(dict[str, Any], body) if isinstance(body, dict) else {}


def _list(body: Any) -> list[dict[str, Any]]:
    items = cast(list[Any], body) if isinstance(body, list) else []
    return [cast(dict[str, Any], x) for x in items if isinstance(x, dict)]


# --- The accounts ----------------------------------------------------------------------


class Stores:
    """The vault plus the three services, for the admin page and the CLI."""

    def __init__(
        self,
        root: Path,
        repo: str,
        client: httpx.AsyncClient | None = None,
        release_dir: Path | None = None,
        release_out: Path | None = None,
    ) -> None:
        self.vault = Vault(root)
        self.repo = repo
        self._client = client
        # The hub writes only the request; the root helper answers in a folder of its own
        # that the hub can read but not change (no symlink tricks on root).
        self.release_dir = release_dir or root.parent / "android-release"
        self.release_out = release_out or RELEASE_OUT
        self.listing = LISTING
        self.screenshot_dir = root / "screenshots"

    def _http(self) -> httpx.AsyncClient:
        return self._client or httpx.AsyncClient(timeout=TIMEOUT)

    async def _close(self, client: httpx.AsyncClient) -> None:
        if client is not self._client:
            await client.aclose()

    # Google Play

    async def _play_token(self, client: httpx.AsyncClient) -> str:
        key = _dict(json.loads(self.vault.secret("play", "Google-Token")))
        now = int(time.time())
        assertion = jwt_rs256(
            str(key["private_key"]).encode(),
            {
                "iss": key["client_email"],
                "scope": PLAY_SCOPE,
                "aud": key["token_uri"],
                "iat": now,
                "exp": now + 3600,
            },
        )
        r = await client.post(
            str(key["token_uri"]),
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        if r.status_code != 200:
            raise StoreError(f"Google lehnt das Dienstkonto ab ({_message(r)}).")
        token = _dict(r.json()).get("access_token")
        if not isinstance(token, str):
            raise StoreError("Google gab kein Zugriffstoken zurück.")
        return token

    async def _check_play(self, client: httpx.AsyncClient, report: Report) -> None:
        title = TITLES["play"]
        if not self.vault.has("play"):
            report.add(title, "warn", "Noch nicht hochgeladen.")
            return
        try:
            token = await self._play_token(client)
        except StoreError as exc:
            report.add(title, "fail", str(exc))
            return
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.post(f"{PLAY}/applications/{PACKAGE}/edits", headers=auth)
        if r.status_code == 200:
            edit = _dict(r.json()).get("id")
            if isinstance(edit, str):
                await client.delete(f"{PLAY}/applications/{PACKAGE}/edits/{edit}", headers=auth)
            report.add(title, "ok", f"Zugriff auf {PACKAGE} in der Play Console.")
        elif r.status_code == 404:
            report.add(
                title,
                "warn",
                "Angemeldet, aber Google kennt die App über die Schnittstelle noch nicht: das "
                "erste App Bundle einmal von Hand in der Play Console hochladen (Interner "
                "Test). Danach lädt der Server selbst hoch.",
            )
        elif r.status_code in (401, 403):
            report.add(
                title,
                "fail",
                "Angemeldet, aber ohne Rechte an der App: in der Play Console unter Nutzer "
                "und Berechtigungen das Dienstkonto einladen und Releases erlauben "
                f"({_message(r)}).",
            )
        else:
            report.add(title, "fail", _message(r))

    # App Store Connect

    def _asc_auth(self) -> dict[str, str]:
        meta = self.vault.meta("asc") or {}
        now = int(time.time())
        token = jwt_es256(
            self.vault.secret("asc", "App-Store-Connect-Token"),
            str(meta["key_id"]),
            {"iss": meta["issuer_id"], "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        )
        return {"Authorization": f"Bearer {token}"}

    async def _check_asc(self, client: httpx.AsyncClient, report: Report) -> None:
        title = TITLES["asc"]
        if not self.vault.has("asc"):
            report.add(title, "warn", "Noch nicht hochgeladen.")
            return
        auth = self._asc_auth()
        r = await client.get(
            f"{ASC}/bundleIds", params={"filter[identifier]": PACKAGE}, headers=auth
        )
        if r.status_code != 200:
            report.add(title, "fail", f"Apple lehnt den Schlüssel ab ({_message(r)}).")
            return
        found = [
            b for b in _list(_dict(r.json()).get("data"))
            if _dict(b.get("attributes")).get("identifier") == PACKAGE
        ]  # fmt: skip
        if found:
            report.add("Bundle-ID", "ok", f"{PACKAGE} ist bei Apple registriert.")
        else:
            body = {
                "data": {
                    "type": "bundleIds",
                    "attributes": {"identifier": PACKAGE, "name": APP_NAME, "platform": "IOS"},
                }
            }
            r = await client.post(f"{ASC}/bundleIds", json=body, headers=auth)
            if r.status_code != 201:
                report.add("Bundle-ID", "fail", f"Anlegen ging nicht ({_message(r)}).")
                return
            report.add("Bundle-ID", "ok", f"{PACKAGE} bei Apple angelegt.")
        r = await client.get(f"{ASC}/apps", params={"filter[bundleId]": PACKAGE}, headers=auth)
        if r.status_code == 200 and _list(_dict(r.json()).get("data")):
            report.add(title, "ok", "Die App ist in App Store Connect angelegt.")
        elif r.status_code == 200:
            report.add(
                title,
                "warn",
                "Schlüssel gilt. Die App selbst kann nur von Hand angelegt werden: App Store "
                f"Connect → Apps → + → Neue App, Bundle-ID {PACKAGE}, Name allskyhub.",
            )
        else:
            report.add(title, "fail", _message(r))

    # Codemagic

    def _cm_auth(self, purpose: str) -> dict[str, str]:
        return {"x-auth-token": self.vault.secret("codemagic", purpose).decode()}

    async def _cm_app(self, client: httpx.AsyncClient, report: Report) -> str | None:
        """The Codemagic app of the repository: the one there, or added now."""
        auth = self._cm_auth("Codemagic-App")
        r = await client.get(f"{CODEMAGIC}/user/apps", params={"page_size": 100}, headers=auth)
        if r.status_code != 200:
            report.add(
                TITLES["codemagic"], "fail", f"Codemagic lehnt das Token ab ({_message(r)})."
            )
            return None
        for app in _list(_dict(r.json()).get("data")):
            if app.get("name") == APP_NAME and not app.get("archived"):
                app_id = str(app["id"])
                report.add(TITLES["codemagic"], "ok", "Die App ist in Codemagic vorhanden.")
                return app_id
        body: dict[str, Any] = {"repositoryUrl": f"https://github.com/{self.repo}.git"}
        r = await client.get(f"{CODEMAGIC}/user/teams", headers=auth)
        teams = _list(_dict(r.json()).get("data")) if r.status_code == 200 else []
        if len(teams) == 1:
            body["teamId"] = teams[0]["id"]
        r = await client.post(f"{CODEMAGIC_V1}/apps", json=body, headers=auth)
        app_id = _dict(r.json()).get("_id") if r.status_code in (200, 201) else None
        if not isinstance(app_id, str):
            report.add(TITLES["codemagic"], "fail", f"App anlegen ging nicht ({_message(r)}).")
            return None
        where = f" im Team {teams[0].get('name')}" if len(teams) == 1 else ""
        report.add(TITLES["codemagic"], "ok", f"Die App in Codemagic angelegt{where}.")
        return app_id

    def _variables(self) -> dict[str, str]:
        """The build secrets for codemagic.yaml, as far as they are there."""
        out: dict[str, str] = {}
        meta = self.vault.meta("asc")
        if meta is not None:
            out["APP_STORE_CONNECT_PRIVATE_KEY"] = self.vault.secret(
                "asc", "Codemagic-Gruppe"
            ).decode()
            out["APP_STORE_CONNECT_KEY_IDENTIFIER"] = str(meta["key_id"])
            out["APP_STORE_CONNECT_ISSUER_ID"] = str(meta["issuer_id"])
        out["CERTIFICATE_PRIVATE_KEY"] = self.vault.secret(
            "ios_certificate", "Codemagic-Gruppe"
        ).decode()
        return out

    async def _cm_group(self, client: httpx.AsyncClient, app_id: str, report: Report) -> None:
        auth = self._cm_auth("Codemagic-Gruppe")
        title = f"Codemagic-Gruppe {GROUP}"
        r = await client.get(
            f"{CODEMAGIC}/apps/{app_id}/variable-groups", params={"page_size": 100}, headers=auth
        )
        if r.status_code != 200:
            report.add(title, "fail", _message(r))
            return
        group = next(
            (g["id"] for g in _list(_dict(r.json()).get("data")) if g.get("name") == GROUP), None
        )
        if group is None:
            r = await client.post(
                f"{CODEMAGIC}/apps/{app_id}/variable-groups", json={"name": GROUP}, headers=auth
            )
            group = _dict(_dict(r.json()).get("data")).get("id") if r.status_code < 300 else None
            if not isinstance(group, str):
                report.add(title, "fail", f"Anlegen ging nicht ({_message(r)}).")
                return
        r = await client.get(
            f"{CODEMAGIC}/variable-groups/{group}/variables",
            params={"page_size": 100},
            headers=auth,
        )
        if r.status_code != 200:
            report.add(title, "fail", _message(r))
            return
        there = {str(v["name"]): str(v["id"]) for v in _list(_dict(r.json()).get("data"))}
        variables = self._variables()
        for name in sorted(OBSOLETE & there.keys()):  # Android keys stay on the hub now
            r = await client.delete(
                f"{CODEMAGIC}/variable-groups/{group}/variables/{there.pop(name)}", headers=auth
            )
            if r.status_code >= 300 and r.status_code != 404:
                report.add(title, "fail", f"{name} entfernen: {_message(r)}")
                return
        new = [{"name": k, "value": v} for k, v in variables.items() if k not in there]
        for name, value in variables.items():
            if name in there:
                r = await client.patch(
                    f"{CODEMAGIC}/variable-groups/{group}/variables/{there[name]}",
                    json={"value": value, "secure": True},
                    headers=auth,
                )
                if r.status_code >= 300:
                    report.add(title, "fail", f"{name}: {_message(r)}")
                    return
        if new:
            r = await client.post(
                f"{CODEMAGIC}/variable-groups/{group}/variables",
                json={"secure": True, "variables": new},
                headers=auth,
            )
            if r.status_code >= 300:
                report.add(title, "fail", _message(r))
                return
        report.add(
            title, "ok", f"{len(variables)} geheime Variablen hinterlegt: {', '.join(variables)}."
        )

    # The whole round

    def ensure_keys(self, report: Report, by: str) -> None:
        for kind, make in (
            ("android_upload", make_android_upload),
            ("ios_certificate", make_ios_certificate_key),
        ):
            if self.vault.has(kind):
                continue
            secret, meta = make()
            self.vault.put(kind, secret, meta, by)
            report.add(TITLES[kind], "ok", "Neu erzeugt.")

    async def setup(self, by: str) -> Report:
        """Check every account and set up what the API can set up."""
        report = Report()
        self.ensure_keys(report, by)
        client = self._http()
        try:
            for check in (self._check_play, self._check_asc):
                try:
                    await check(client, report)
                except httpx.HTTPError as exc:
                    report.add("Verbindung", "fail", f"{type(exc).__name__}")
            if not self.vault.has("codemagic"):
                report.add(TITLES["codemagic"], "warn", "Noch nicht hochgeladen.")
                return report
            try:
                app_id = await self._cm_app(client, report)
                if app_id:
                    self.vault.update_meta("codemagic", app_id=app_id)
                    await self._cm_group(client, app_id, report)
            except httpx.HTTPError as exc:
                report.add("Codemagic", "fail", f"{type(exc).__name__}")
        finally:
            await self._close(client)
        return report

    async def build(self, platform: str, by: str, branch: str = "main") -> str:
        """Start a release build; the build id (Codemagic's, or "local")."""
        if platform == "android":
            self.request_android(by)
            return "local"
        meta = self.vault.meta("codemagic") or {}
        app_id = meta.get("app_id")
        if not isinstance(app_id, str):
            raise StoreError("Erst „Prüfen und einrichten“, dann bauen.")
        client = self._http()
        try:
            r = await client.post(
                f"{CODEMAGIC}/apps/{app_id}/builds",
                json={"workflow_id": WORKFLOWS[platform], "branch": branch},
                headers=self._cm_auth(f"Build {platform}"),
            )
        except httpx.HTTPError as exc:
            raise StoreError(f"Codemagic nicht erreichbar ({type(exc).__name__}).") from None
        finally:
            await self._close(client)
        build_id = _dict(_dict(r.json()).get("data")).get("id") if r.status_code < 300 else None
        if not isinstance(build_id, str):
            raise StoreError(f"Codemagic startet den Build nicht ({_message(r)}).")
        self.vault.add_build(
            {"id": build_id, "app_id": app_id, "platform": platform, "at": _now(), "by": by}
        )
        return build_id

    async def recent_builds(self, limit: int = 3) -> list[dict[str, Any]]:
        """The last builds started here, with Codemagic's current status."""
        builds = self.vault.builds()[:limit]
        local = [i for i, b in enumerate(builds) if b.get("id") == "local"]
        if local:  # only the newest local build has a state; older ones are done
            builds[local[0]] = {**builds[local[0]], "status": self.android_job().state}
        if not self.vault.has("codemagic"):
            return builds
        client = self._http()
        out: list[dict[str, Any]] = []
        try:
            auth = self._cm_auth("Build-Status")
            for b in builds:
                if b.get("id") == "local":
                    out.append(b)
                    continue
                status = "unbekannt"
                try:
                    r = await client.get(f"{CODEMAGIC}/builds/{b['id']}", headers=auth)
                    if r.status_code == 200:
                        status = str(_dict(_dict(r.json()).get("data")).get("status", status))
                except httpx.HTTPError:
                    pass
                out.append({**b, "status": status})
        finally:
            await self._close(client)
        return out

    # Android on the hub's server

    def request_android(self, by: str) -> None:
        """Leave a request for the root helper (allskyhub-android-release.path)."""
        if not self.vault.has("android_upload"):
            raise StoreError("Erst „Prüfen und einrichten“, dann bauen.")
        if self.android_job().busy:
            raise StoreError("Es läuft schon ein Android-Build.")
        self.release_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        (self.release_dir / "request").write_text(f"{by}\n")
        self.vault.add_build({"id": "local", "platform": "android", "at": _now(), "by": by})
        log.info("android release requested by %s", by)

    def android_job(self) -> AndroidJob:
        def read(name: str) -> str:
            try:
                return (self.release_out / name).read_text()
            except OSError:
                return ""

        state = read("state").strip() or "idle"
        if (self.release_dir / "request").exists():
            state = "requested"
        latest: dict[str, Any] | None = None
        try:
            raw = json.loads(read("latest.json"))
            if isinstance(raw, dict):
                latest = cast(dict[str, Any], raw)
        except ValueError:
            pass
        name = str(latest.get("file", "")) if latest else ""
        if latest and not (re.fullmatch(r"allskyhub-\d+\.aab", name) and self.aab(name)):
            latest = None
        return AndroidJob(state, "\n".join(read("log").splitlines()[-LOG_TAIL:]), latest)

    def aab(self, name: str) -> Path | None:
        path = self.release_out / name
        ok = re.fullmatch(r"allskyhub-\d+\.aab", name) and path.is_file()
        return path if ok else None

    async def play_upload(self, aab: Path) -> tuple[int, str]:
        """Send an App Bundle to the internal test track: (0, text) when it is there,
        (3, text) when Google does not know the app yet (the first one goes by hand)."""
        if not self.vault.has("play"):
            return 1, "Kein Google-Play-Dienstkonto hinterlegt."
        client = self._client or httpx.AsyncClient(timeout=UPLOAD_TIMEOUT)
        try:
            return await self._play_upload(client, aab)
        except StoreError as exc:
            return 1, str(exc)
        except httpx.HTTPError as exc:
            return 1, f"Google Play nicht erreichbar ({type(exc).__name__})."
        finally:
            await self._close(client)

    async def _play_upload(self, client: httpx.AsyncClient, aab: Path) -> tuple[int, str]:
        auth = {"Authorization": f"Bearer {await self._play_token(client)}"}
        app = f"{PLAY}/applications/{PACKAGE}"
        r = await client.post(f"{app}/edits", headers=auth)
        if r.status_code == 404:
            return 3, (
                "Google kennt die App über die Schnittstelle noch nicht. Diese erste Version "
                "bitte von Hand in der Play Console hochladen (Testen → Interner Test → Neuen "
                "Release erstellen); die Datei liegt auf der Admin-Seite. Danach geht es "
                "von selbst."
            )
        if r.status_code != 200:
            raise StoreError(f"Bearbeitung nicht möglich ({_message(r)}).")
        edit = str(_dict(r.json())["id"])
        bundle = await asyncio.to_thread(aab.read_bytes)
        r = await client.post(
            f"{PLAY_UPLOAD}/applications/{PACKAGE}/edits/{edit}/bundles",
            params={"uploadType": "media"},
            content=bundle,
            headers={**auth, "Content-Type": "application/octet-stream"},
        )
        if r.status_code != 200:
            raise StoreError(f"Upload abgelehnt ({_message(r)}).")
        code = _dict(r.json()).get("versionCode")
        release: dict[str, Any] = {"versionCodes": [str(code)], "status": "completed"}
        track = f"{app}/edits/{edit}/tracks/{TRACK}"
        r = await client.put(track, json={"track": TRACK, "releases": [release]}, headers=auth)
        if r.status_code == 400 and "draft" in r.text.lower():
            # An app nobody can install yet takes draft releases only.
            release["status"] = "draft"
            r = await client.put(track, json={"track": TRACK, "releases": [release]}, headers=auth)
        if r.status_code != 200:
            raise StoreError(f"Interner Test nicht gesetzt ({_message(r)}).")
        r = await client.post(f"{app}/edits/{edit}:commit", headers=auth)
        if r.status_code != 200:
            raise StoreError(f"Abschließen abgelehnt ({_message(r)}).")
        if release["status"] == "draft":
            return 0, (
                f"Version {code} liegt als Entwurf im internen Test. Solange die App noch nie "
                "veröffentlicht war, gibt Google sie nur in der Play Console frei "
                "(Interner Test → Release prüfen → Einführen)."
            )
        return 0, f"Version {code} ist im internen Test."

    # Store listing

    def screenshots(self) -> list[Path]:
        if not self.screenshot_dir.is_dir():
            return []
        return sorted(p for p in self.screenshot_dir.iterdir() if p.suffix in (".png", ".jpg"))

    def put_screenshots(self, files: list[bytes]) -> None:
        """Replace the phone screenshots (2 to 8, PNG or JPEG, as Play takes them)."""
        low, high = SCREENSHOTS
        if not low <= len(files) <= high:
            raise StoreError(f"Google Play nimmt {low} bis {high} Screenshots.")
        named: list[tuple[str, bytes]] = []
        for i, data in enumerate(files, 1):
            size, suffix = image_size(data)
            if size is None:
                raise StoreError(f"Bild {i} ist kein PNG oder JPEG.")
            short, long = sorted(size)
            if len(data) > SCREENSHOT_MAX or short < 320 or long > 3840 or long > 2 * short:
                raise StoreError(
                    f"Bild {i} passt nicht: 320 bis 3840 Pixel, die lange Seite höchstens "
                    "doppelt so lang wie die kurze, bis 8 MB."
                )
            named.append((f"{i:02d}{suffix}", data))
        self.screenshot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for old in self.screenshots():
            old.unlink()
        for name, data in named:
            (self.screenshot_dir / name).write_bytes(data)

    def listing_texts(self, language: str) -> dict[str, str]:
        folder = self.listing / language
        return {
            "language": language,
            "title": (folder / "title.txt").read_text().strip(),
            "shortDescription": (folder / "short_description.txt").read_text().strip(),
            "fullDescription": (folder / "full_description.txt").read_text().strip(),
        }

    async def push_listing(self) -> Report:
        """Texts, icon, feature graphic and screenshots of every language to Play."""
        report = Report()
        if not self.vault.has("play"):
            report.add("Store-Eintrag", "warn", "Kein Google-Play-Dienstkonto hinterlegt.")
            return report
        client = self._client or httpx.AsyncClient(timeout=UPLOAD_TIMEOUT)
        try:
            await self._push_listing(client, report)
        except StoreError as exc:
            report.add("Store-Eintrag", "fail", str(exc))
        except httpx.HTTPError as exc:
            report.add(
                "Store-Eintrag", "fail", f"Google Play nicht erreichbar ({type(exc).__name__})."
            )
        finally:
            await self._close(client)
        return report

    async def _push_listing(self, client: httpx.AsyncClient, report: Report) -> None:
        auth = {"Authorization": f"Bearer {await self._play_token(client)}"}
        app = f"{PLAY}/applications/{PACKAGE}"
        r = await client.post(f"{app}/edits", headers=auth)
        if r.status_code == 404:
            raise StoreError(
                "Google kennt die App über die Schnittstelle noch nicht: erst das erste App "
                "Bundle von Hand hochladen."
            )
        if r.status_code != 200:
            raise StoreError(f"Bearbeitung nicht möglich ({_message(r)}).")
        edit = f"{app}/edits/{_dict(r.json())['id']}"
        upload = f"{PLAY_UPLOAD}/applications/{PACKAGE}/edits/{_dict(r.json())['id']}"
        details = {
            "defaultLanguage": LANGUAGES[0],
            "contactEmail": CONTACT_EMAIL,
            "contactWebsite": CONTACT_WEBSITE,
        }
        r = await client.put(f"{edit}/details", json=details, headers=auth)
        if r.status_code != 200:
            raise StoreError(f"Kontaktdaten abgelehnt ({_message(r)}).")
        shots = self.screenshots()
        for language in LANGUAGES:
            texts = self.listing_texts(language)
            r = await client.put(f"{edit}/listings/{language}", json=texts, headers=auth)
            if r.status_code != 200:
                raise StoreError(f"Texte {language} abgelehnt ({_message(r)}).")
            images: list[tuple[str, Path]] = [
                ("icon", self.listing / "icon.png"),
                ("featureGraphic", self.listing / language / "feature.png"),
                *(("phoneScreenshots", p) for p in shots),
            ]
            for kind in dict.fromkeys(k for k, _ in images):
                r = await client.delete(f"{edit}/listings/{language}/{kind}", headers=auth)
                if r.status_code not in (200, 204, 404):
                    raise StoreError(f"{kind} {language}: {_message(r)}")
            for kind, path in images:
                data = await asyncio.to_thread(path.read_bytes)
                kind_type = "image/png" if path.suffix == ".png" else "image/jpeg"
                r = await client.post(
                    f"{upload}/listings/{language}/{kind}",
                    params={"uploadType": "media"},
                    content=data,
                    headers={**auth, "Content-Type": kind_type},
                )
                if r.status_code != 200:
                    raise StoreError(f"{kind} {language} abgelehnt ({_message(r)}).")
            report.add(
                f"Store-Eintrag {language}",
                "ok",
                f"„{texts['title']}“: Texte, Icon, Grafik"
                + (f" und {len(shots)} Screenshots" if shots else ", noch ohne Screenshots"),
            )
        r = await client.post(f"{edit}:commit", headers=auth)
        if r.status_code != 200:
            raise StoreError(f"Abschließen abgelehnt ({_message(r)}).")
        if len(shots) < SCREENSHOTS[0]:
            report.add(
                "Screenshots",
                "warn",
                "Für die Veröffentlichung braucht Google mindestens zwei Handy-Screenshots: "
                "unten hochladen und den Store-Eintrag noch einmal übertragen.",
            )


def image_size(data: bytes) -> tuple[tuple[int, int] | None, str]:
    """(width, height) and suffix of a PNG or JPEG, (None, "") for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        return (int.from_bytes(data[16:20]), int.from_bytes(data[20:24])), ".png"
    if data.startswith(b"\xff\xd8"):
        return jpeg_size(data), ".jpg"
    return None, ""


@dataclass(frozen=True)
class AndroidJob:
    state: str  # idle, requested, running, ok, built, failed
    log: str
    latest: dict[str, Any] | None  # the newest App Bundle (latest.json)

    @property
    def busy(self) -> bool:
        return self.state in ("requested", "running")


def build_url(build: dict[str, Any]) -> str | None:
    if build.get("id") == "local":
        return None
    return f"https://codemagic.io/app/{build.get('app_id')}/build/{build.get('id')}"
