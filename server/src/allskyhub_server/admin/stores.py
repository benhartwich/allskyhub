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
import plistlib
import re
import secrets as _random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from xml.parsers.expat import ExpatError

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
# The store listing: texts, graphics and screenshots in the repository (app/store/play);
# screenshots uploaded on the admin page take the place of the repository's.
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
    "youtube": "YouTube-OAuth-Client",
    "fcm": "Firebase-Dienstkonto (Push)",
    "turnstile": "Cloudflare Turnstile",
    "github": "GitHub-Token (nur lesen)",
    # Further apps' own upload keys (OtherApp.android_upload), made on their setup.
    "bortle_android_upload": "bortle: Android-Upload-Schlüssel",
}
UPLOADED = ("play", "asc", "codemagic", "youtube", "fcm", "turnstile", "github")
GITHUB_TOKEN = re.compile(r"^(github_pat_|ghp_)[A-Za-z0-9_]{20,255}$")
# The app's own Firebase registration (public identifiers, not in the vault's secrets).
FCM_APP_FIELDS = ("sender_id", "android_app_id", "android_api_key", "ios_app_id", "ios_api_key")
FCM_APP_FILE = "fcm_app.json"
FCM_ID = re.compile(r"^[A-Za-z0-9:._-]{1,200}$")
TURNSTILE_KEY = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
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


@dataclass(frozen=True)
class OtherApp:
    """A further iOS app of the same developer accounts (Benjamin, 2026-10-28: one App
    Store Connect key and one Codemagic token for all). The hub registers its bundle id,
    adds its repository to Codemagic and fills its variable group with the same keys:
    nobody uploads them again, and they go only to Codemagic, as secure variables."""

    key: str
    title: str
    package: str
    name: str  # the bundle id's name at Apple
    repo: str  # owner/name on GitHub
    codemagic_name: str  # Codemagic names an app after its repository
    group: str
    workflow: str = "ios-release"
    # Its store listings, from ``store/`` of the repository's main branch (layout as
    # app/store here): contact, links, App Store categories and the first version.
    contact_email: str = ""
    website: str = ""
    privacy_url: str = ""
    categories: tuple[str, str] = ("", "")
    version: str = "1.0.0"
    what_to_test: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def fcm_file(self) -> str:
        """Its Firebase identifiers (from the same google-services.json and plists)."""
        return f"fcm_app_{self.key}.json"

    @property
    def android_upload(self) -> str:
        """The vault kind of its own Play upload key (built on the hub's server like
        allskyhub's, deploy/android: the request's first line is ``app=<key>``)."""
        return f"{self.key}_android_upload"


OTHER_APPS = {
    "bortle": OtherApp(
        key="bortle",
        title="bortle",
        package="eu.bortle.app",
        name="bortle",
        repo="benhartwich/bortle-app",
        codemagic_name="bortle-app",
        group="bortle_store",
        contact_email="info@bortle.eu",
        website="https://bortle.eu",
        privacy_url="https://bortle.eu/privacy",
        categories=("PHOTO_AND_VIDEO", "UTILITIES"),
        version="0.2.0",
        what_to_test={
            "de-DE": "Anmelden bei bortle.eu oder dem eigenen Server, Sitzungen planen und "
            "verfolgen. Rückmeldungen gern über TestFlight.",
            "en-US": "Sign in to bortle.eu or your own server, plan and follow sessions. "
            "Feedback is welcome through TestFlight.",
        },
    ),
}
# A repository's store folder, downloaded for a listing push (codeload, no API quota).
LISTING_TARBALL = "https://codeload.github.com/{repo}/tar.gz/refs/heads/main"
LISTING_MAX_BYTES = 200_000_000


def extract_listing(tarball: bytes, dest: Path) -> Path:
    """Unpack only ``store/`` of a GitHub tarball into ``dest`` (replaced), with the
    tarfile data filter: no links, devices or paths outside. Returns ``dest/store``."""
    import io
    import shutil
    import tarfile

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        members: list[tarfile.TarInfo] = []
        for m in tar.getmembers():
            parts = m.name.split("/", 1)  # "<repo>-<sha>/store/..."
            if len(parts) == 2 and parts[1].startswith("store/") and (m.isfile() or m.isdir()):
                m.name = parts[1]
                members.append(m)
        tar.extractall(dest, members=members, filter="data")
    store = dest / "store"
    if not store.is_dir():
        raise StoreError("Im Repository fehlt der Ordner store/.")
    return store


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

    def write_json(self, name: str, data: dict[str, Any]) -> None:
        self._write(name, json.dumps(data).encode())

    def read_json(self, name: str) -> dict[str, Any] | None:
        try:
            raw = json.loads((self.root / name).read_text())
        except (OSError, ValueError):
            return None
        return cast(dict[str, Any], raw) if isinstance(raw, dict) else None

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


def parse_fcm(raw: bytes) -> tuple[bytes, dict[str, Any]]:
    """The Firebase service account that sends push (Cloud Messaging)."""
    try:
        info = json.loads(raw)
    except ValueError:
        raise StoreError("Das ist keine JSON-Datei.") from None
    data = cast(dict[str, Any], info) if isinstance(info, dict) else {}
    if data.get("type") != "service_account":
        raise StoreError("Bitte den JSON-Schlüssel eines Dienstkontos hochladen.")
    try:
        _rsa_key(str(data["private_key"]).encode())
        project, email = str(data["project_id"]), str(data["client_email"])
    except (KeyError, StoreError, ValueError):
        raise StoreError(
            "Im Schlüssel fehlt etwas (project_id, client_email, private_key)."
        ) from None
    return raw, {"project_id": project, "client_email": email}


def parse_turnstile(site_key: str, secret: str) -> tuple[bytes, dict[str, Any]]:
    site_key, secret = site_key.strip(), secret.strip()
    if not TURNSTILE_KEY.match(site_key) or not TURNSTILE_KEY.match(secret):
        raise StoreError("Site-Key und Secret-Key bitte aus dem Turnstile-Widget kopieren.")
    return secret.encode(), {"site_key": site_key}


def parse_fcm_app(fields: dict[str, str]) -> dict[str, str]:
    out = {k: fields.get(k, "").strip() for k in FCM_APP_FIELDS}
    for key, value in out.items():
        if value and not FCM_ID.match(value):
            raise StoreError(f"{key} sieht nicht richtig aus.")
    if not out["sender_id"]:
        raise StoreError("Die Sender-ID fehlt.")
    return out


def parse_google_services(raw: bytes, package: str = PACKAGE) -> dict[str, str]:
    """``google-services.json`` from the Firebase console: the project number and the
    Android app's id and API key. It lists every Android app of the project (bortle's
    too), so only ``package``'s entry counts."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise StoreError("google-services.json ist keine JSON-Datei.") from None
    root = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    sender = str(_dict(root.get("project_info")).get("project_number", ""))
    for c in _list(root.get("client")):
        info = _dict(c.get("client_info"))
        if _dict(info.get("android_client_info")).get("package_name") != package:
            continue
        keys = _list(c.get("api_key"))
        key = keys[0].get("current_key", "") if keys else ""
        return {
            "project_id": str(_dict(root.get("project_info")).get("project_id", "")),
            "sender_id": sender,
            "android_app_id": str(info.get("mobilesdk_app_id", "")),
            "android_api_key": str(key),
        }
    raise StoreError(f"In google-services.json fehlt die Android-App {package}.")


def parse_service_info(raw: bytes, package: str = PACKAGE) -> dict[str, str]:
    """``GoogleService-Info.plist`` of the iOS app ``package`` from the Firebase console."""
    try:
        data: object = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, ExpatError):
        raise StoreError("GoogleService-Info.plist ist keine Plist-Datei.") from None
    info = _dict(data)
    if info.get("BUNDLE_ID") != package:
        raise StoreError(f"GoogleService-Info.plist gehört nicht zur iOS-App {package}.")
    return {
        "project_id": str(info.get("PROJECT_ID", "")),
        "sender_id": str(info.get("GCM_SENDER_ID", "")),
        "ios_app_id": str(info.get("GOOGLE_APP_ID", "")),
        "ios_api_key": str(info.get("API_KEY", "")),
    }


def plist_package(raw: bytes) -> str:
    """The bundle id a GoogleService-Info.plist belongs to ("" when unreadable)."""
    try:
        return str(_dict(plistlib.loads(raw)).get("BUNDLE_ID", ""))
    except (plistlib.InvalidFileException, ValueError, ExpatError):
        return ""


def fcm_defines(app: OtherApp, ids: dict[str, Any] | None) -> dict[str, str]:
    """A further app's Firebase identifiers as its build's ``--dart-define`` names
    (``BORTLE_FCM_SENDER_ID`` …); public, like the files they come from."""
    out: dict[str, str] = {}
    for field_name in ("project_id", *FCM_APP_FIELDS):
        value = str((ids or {}).get(field_name, ""))
        if value and FCM_ID.match(value):
            out[f"{app.key.upper()}_FCM_{field_name.upper()}"] = value
    return out


def parse_github(token: str) -> tuple[bytes, dict[str, Any]]:
    """A read-only GitHub token: 5000 API requests an hour instead of 60 per address."""
    token = token.strip()
    if not GITHUB_TOKEN.match(token):
        raise StoreError("Das sieht nicht wie ein GitHub-Token aus (github_pat_… oder ghp_…).")
    return token.encode(), {}


def github_token(vault: Vault) -> str | None:
    if not vault.has("github"):
        return None
    return vault.secret("github", "GitHub-API").decode()


def parse_codemagic(token: str) -> tuple[bytes, dict[str, Any]]:
    token = token.strip()
    if not TOKEN.match(token):
        raise StoreError("Das sieht nicht wie ein Codemagic-API-Token aus.")
    return token.encode(), {}


def parse_youtube(raw: bytes, redirect_uri: str) -> tuple[bytes, dict[str, Any]]:
    """Roadmap H5: the OAuth client (type web application) the owners connect their YouTube
    channels with; Google Cloud → APIs & Services → Credentials → Download JSON."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise StoreError("Das ist keine JSON-Datei.") from None
    web: object = cast(dict[str, Any], data).get("web") if isinstance(data, dict) else None
    if not isinstance(web, dict):
        raise StoreError(
            "Das ist kein OAuth-Client vom Typ „Webanwendung“ (in der Datei fehlt „web“)."
        )
    client = cast(dict[str, Any], web)
    if not all(
        isinstance(client.get(k), str) and client[k] for k in ("client_id", "client_secret")
    ):
        raise StoreError("In der Datei fehlen client_id oder client_secret.")
    uris: object = client.get("redirect_uris") or []
    if not isinstance(uris, list) or redirect_uri not in cast(list[object], uris):
        raise StoreError(
            f"Beim OAuth-Client fehlt der autorisierte Weiterleitungs-URI {redirect_uri}. "
            "Bitte in Google Cloud eintragen und die JSON-Datei neu herunterladen."
        )
    return raw, {"client_id": client["client_id"], "project_id": client.get("project_id")}


# --- What the hub makes itself ---------------------------------------------------------


def make_android_upload(app: str = "allskyhub") -> tuple[bytes, dict[str, Any]]:
    """The upload key for Play App Signing: RSA 4096 in a PKCS#12 keystore, alias
    ``upload``, valid for 30 years (Play wants validity past 2033)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, f"{app} upload"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, app),
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
        self._listing_task: asyncio.Task[Report] | None = None
        self.jobs: dict[str, asyncio.Task[Report]] = {}  # background pushes (appstore.py)
        self.screenshot_dir = root / "screenshots"

    @property
    def client(self) -> httpx.AsyncClient | None:
        """The client given for tests, else None (each call makes its own)."""
        return self._client

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

    def asc_auth(self) -> dict[str, str]:
        meta = self.vault.meta("asc") or {}
        now = int(time.time())
        token = jwt_es256(
            self.vault.secret("asc", "App-Store-Connect-Token"),
            str(meta["key_id"]),
            {"iss": meta["issuer_id"], "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        )
        return {"Authorization": f"Bearer {token}"}

    async def _check_asc(
        self,
        client: httpx.AsyncClient,
        report: Report,
        package: str = PACKAGE,
        name: str = APP_NAME,
    ) -> None:
        title = TITLES["asc"]
        if not self.vault.has("asc"):
            report.add(title, "warn", "Noch nicht hochgeladen.")
            return
        auth = self.asc_auth()
        r = await client.get(
            f"{ASC}/bundleIds", params={"filter[identifier]": package}, headers=auth
        )
        if r.status_code != 200:
            report.add(title, "fail", f"Apple lehnt den Schlüssel ab ({_message(r)}).")
            return
        found = [
            b for b in _list(_dict(r.json()).get("data"))
            if _dict(b.get("attributes")).get("identifier") == package
        ]  # fmt: skip
        if found:
            report.add("Bundle-ID", "ok", f"{package} ist bei Apple registriert.")
            await self._push_capability(client, report, auth, str(found[0]["id"]), package)
        else:
            body = {
                "data": {
                    "type": "bundleIds",
                    "attributes": {"identifier": package, "name": name, "platform": "IOS"},
                }
            }
            r = await client.post(f"{ASC}/bundleIds", json=body, headers=auth)
            if r.status_code != 201:
                report.add("Bundle-ID", "fail", f"Anlegen ging nicht ({_message(r)}).")
                return
            report.add("Bundle-ID", "ok", f"{package} bei Apple angelegt.")
        r = await client.get(f"{ASC}/apps", params={"filter[bundleId]": package}, headers=auth)
        if r.status_code == 200 and _list(_dict(r.json()).get("data")):
            report.add(title, "ok", "Die App ist in App Store Connect angelegt.")
        elif r.status_code == 200:
            report.add(
                title,
                "warn",
                "Schlüssel gilt. Die App selbst kann nur von Hand angelegt werden: App Store "
                f"Connect → Apps → + → Neue App, Bundle-ID {package}, Name {name}.",
            )
        else:
            report.add(title, "fail", _message(r))

    async def _push_capability(
        self,
        client: httpx.AsyncClient,
        report: Report,
        auth: dict[str, str],
        bundle_id: str,
        package: str,
    ) -> None:
        """Push notifications for the bundle id (stage 5). Switching it on makes the App
        Store provisioning profiles outdated: they are deleted, so the next release build
        (fetch-signing-files --create) makes a new one with the capability."""
        title = "Push-Benachrichtigungen"
        r = await client.get(f"{ASC}/bundleIds/{bundle_id}/bundleIdCapabilities", headers=auth)
        if r.status_code != 200:
            report.add(title, "warn", f"Nicht prüfbar ({_message(r)}).")
            return
        kinds = {
            _dict(c.get("attributes")).get("capabilityType")
            for c in _list(_dict(r.json()).get("data"))
        }
        if "PUSH_NOTIFICATIONS" in kinds:
            report.add(title, "ok", f"Für {package} eingeschaltet.")
            return
        body = {
            "data": {
                "type": "bundleIdCapabilities",
                "attributes": {"capabilityType": "PUSH_NOTIFICATIONS"},
                "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": bundle_id}}},
            }
        }
        r = await client.post(f"{ASC}/bundleIdCapabilities", json=body, headers=auth)
        if r.status_code != 201:
            report.add(title, "fail", f"Einschalten ging nicht ({_message(r)}).")
            return
        r = await client.get(f"{ASC}/bundleIds/{bundle_id}/profiles", headers=auth)
        stale = [str(p["id"]) for p in _list(_dict(r.json()).get("data"))] if r.is_success else []
        for profile in stale:
            await client.delete(f"{ASC}/profiles/{profile}", headers=auth)
        report.add(
            title,
            "ok",
            f"Für {package} eingeschaltet; {len(stale)} alte Profile gelöscht, der nächste "
            "iOS-Build legt ein neues an.",
        )

    # Codemagic

    def _cm_auth(self, purpose: str) -> dict[str, str]:
        return {"x-auth-token": self.vault.secret("codemagic", purpose).decode()}

    async def _cm_app(
        self,
        client: httpx.AsyncClient,
        report: Report,
        name: str = APP_NAME,
        repo: str | None = None,
    ) -> str | None:
        """The Codemagic app of the repository: the one there, or added now."""
        auth = self._cm_auth("Codemagic-App")
        r = await client.get(f"{CODEMAGIC}/user/apps", params={"page_size": 100}, headers=auth)
        if r.status_code != 200:
            report.add(
                TITLES["codemagic"], "fail", f"Codemagic lehnt das Token ab ({_message(r)})."
            )
            return None
        for app in _list(_dict(r.json()).get("data")):
            if app.get("name") == name and not app.get("archived"):
                app_id = str(app["id"])
                report.add(TITLES["codemagic"], "ok", "Die App ist in Codemagic vorhanden.")
                return app_id
        body: dict[str, Any] = {"repositoryUrl": f"https://github.com/{repo or self.repo}.git"}
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

    async def _cm_group(
        self,
        client: httpx.AsyncClient,
        app_id: str,
        report: Report,
        name: str = GROUP,
        public: dict[str, str] | None = None,
    ) -> None:
        """The group's secure variables, plus ``public`` ones in plain sight (a further
        app's Firebase identifiers for its --dart-define)."""
        auth = self._cm_auth("Codemagic-Gruppe")
        title = f"Codemagic-Gruppe {name}"
        r = await client.get(
            f"{CODEMAGIC}/apps/{app_id}/variable-groups", params={"page_size": 100}, headers=auth
        )
        if r.status_code != 200:
            report.add(title, "fail", _message(r))
            return
        group = next(
            (g["id"] for g in _list(_dict(r.json()).get("data")) if g.get("name") == name), None
        )
        if group is None:
            r = await client.post(
                f"{CODEMAGIC}/apps/{app_id}/variable-groups", json={"name": name}, headers=auth
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
        public = public or {}
        for name, value in public.items():
            if name in there:
                r = await client.patch(
                    f"{CODEMAGIC}/variable-groups/{group}/variables/{there[name]}",
                    json={"value": value, "secure": False},
                    headers=auth,
                )
                if r.status_code >= 300:
                    report.add(title, "fail", f"{name}: {_message(r)}")
                    return
        plain = [{"name": k, "value": v} for k, v in public.items() if k not in there]
        if plain:
            r = await client.post(
                f"{CODEMAGIC}/variable-groups/{group}/variables",
                json={"secure": False, "variables": plain},
                headers=auth,
            )
            if r.status_code >= 300:
                report.add(title, "fail", _message(r))
                return
        text = f"{len(variables)} geheime Variablen hinterlegt: {', '.join(variables)}."
        if public:
            text += f" Dazu öffentlich: {', '.join(public)}."
        report.add(title, "ok", text)

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

    async def setup_other(self, app: OtherApp, by: str) -> Report:
        """Like ``setup`` for a further iOS app, with the same Apple key, Codemagic token
        and distribution certificate (Apple allows only a few per team)."""
        report = Report()
        if not self.vault.has("ios_certificate"):
            secret, meta = make_ios_certificate_key()
            self.vault.put("ios_certificate", secret, meta, by)
            report.add(TITLES["ios_certificate"], "ok", "Neu erzeugt.")
        if not self.vault.has(app.android_upload):
            secret, meta = make_android_upload(app.title)
            self.vault.put(app.android_upload, secret, meta, by)
            report.add(TITLES[app.android_upload], "ok", "Neu erzeugt.")
        client = self._http()
        try:
            try:
                await self._check_asc(client, report, app.package, app.name)
            except httpx.HTTPError as exc:
                report.add("Verbindung", "fail", f"{type(exc).__name__}")
            if not self.vault.has("codemagic"):
                report.add(TITLES["codemagic"], "warn", "Noch nicht hochgeladen.")
                return report
            try:
                app_id = await self._cm_app(client, report, app.codemagic_name, app.repo)
                if app_id:
                    self.vault.update_meta("codemagic", **{f"{app.key}_app_id": app_id})
                    public = fcm_defines(app, self.vault.read_json(app.fcm_file))
                    await self._cm_group(client, app_id, report, app.group, public)
            except httpx.HTTPError as exc:
                report.add("Codemagic", "fail", f"{type(exc).__name__}")
        finally:
            await self._close(client)
        return report

    def other_app_id(self, app: OtherApp) -> str | None:
        value = (self.vault.meta("codemagic") or {}).get(f"{app.key}_app_id")
        return value if isinstance(value, str) else None

    async def build(
        self, platform: str, by: str, branch: str = "main", app: OtherApp | None = None
    ) -> str:
        """Start a release build; the build id (Codemagic's, or "local")."""
        if platform == "android":
            self.request_android(by, app)
            return "local"
        meta = self.vault.meta("codemagic") or {}
        app_id = self.other_app_id(app) if app else meta.get("app_id")
        if not isinstance(app_id, str):
            raise StoreError("Erst „Prüfen und einrichten“, dann bauen.")
        workflow = app.workflow if app else WORKFLOWS[platform]
        client = self._http()
        try:
            r = await client.post(
                f"{CODEMAGIC}/apps/{app_id}/builds",
                json={"workflow_id": workflow, "branch": branch},
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
            | ({"app": app.key} if app else {})
        )
        return build_id

    async def recent_builds(self, limit: int = 3) -> list[dict[str, Any]]:
        """The last builds started here, with Codemagic's current status."""
        builds = self.vault.builds()[:limit]
        seen: set[str] = set()
        for i, b in enumerate(builds):
            key = str(b.get("app", ""))
            if b.get("id") != "local" or key in seen:
                continue
            seen.add(key)  # only an app's newest local build has a state; older ones are done
            builds[i] = {**b, "status": self.android_job(OTHER_APPS.get(key)).state}
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

    def request_android(self, by: str, app: OtherApp | None = None) -> None:
        """Leave a request for the root helper (allskyhub-android-release.path); for a
        further app its first line is ``app=<key>``. One build at a time."""
        if not self.vault.has(app.android_upload if app else "android_upload"):
            raise StoreError("Erst „Prüfen und einrichten“, dann bauen.")
        if self.android_busy():
            raise StoreError("Es läuft schon ein Android-Build.")
        self.release_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        (self.release_dir / "request").write_text((f"app={app.key}\n" if app else "") + f"{by}\n")
        self.vault.add_build(
            {"id": "local", "platform": "android", "at": _now(), "by": by}
            | ({"app": app.key} if app else {})
        )
        log.info("android release of %s requested by %s", app.key if app else "allskyhub", by)

    def android_busy(self) -> bool:
        return any(self.android_job(a).busy for a in (None, *OTHER_APPS.values()))

    def _release_out(self, app: OtherApp | None) -> Path:
        """Where the root helper answers for the app (deploy/android: release-<key>)."""
        return self.release_out if app is None else self.release_out.with_name(f"release-{app.key}")

    def _requested(self) -> str | None:
        """The app a waiting request names: "allskyhub", a further app's key, or None."""
        try:
            first = (self.release_dir / "request").read_text().split("\n", 1)[0]
        except OSError:
            return None
        key = first.removeprefix("app=")
        return key if first.startswith("app=") and key in OTHER_APPS else "allskyhub"

    def android_job(self, app: OtherApp | None = None) -> AndroidJob:
        out = self._release_out(app)

        def read(name: str) -> str:
            try:
                return (out / name).read_text()
            except OSError:
                return ""

        state = read("state").strip() or "idle"
        if self._requested() == (app.key if app else "allskyhub"):
            state = "requested"
        latest: dict[str, Any] | None = None
        try:
            raw = json.loads(read("latest.json"))
            if isinstance(raw, dict):
                latest = cast(dict[str, Any], raw)
        except ValueError:
            pass
        name = str(latest.get("file", "")) if latest else ""
        prefix = app.key if app else "allskyhub"
        if latest and not (re.fullmatch(rf"{prefix}-\d+\.aab", name) and self.aab(name)):
            latest = None
        return AndroidJob(state, "\n".join(read("log").splitlines()[-LOG_TAIL:]), latest)

    def aab(self, name: str) -> Path | None:
        """An App Bundle built here, by its name (``<app>-<build>.aab``)."""
        for app in (None, *OTHER_APPS.values()):
            if re.fullmatch(rf"{app.key if app else 'allskyhub'}-\d+\.aab", name):
                path = self._release_out(app) / name
                return path if path.is_file() else None
        return None

    async def play_upload(self, aab: Path, package: str = PACKAGE) -> tuple[int, str]:
        """Send an App Bundle to the internal test track: (0, text) when it is there,
        (3, text) when Google does not know the app yet (the first one goes by hand)."""
        if not self.vault.has("play"):
            return 1, "Kein Google-Play-Dienstkonto hinterlegt."
        client = self._client or httpx.AsyncClient(timeout=UPLOAD_TIMEOUT)
        try:
            return await self._play_upload(client, aab, package)
        except StoreError as exc:
            return 1, str(exc)
        except httpx.HTTPError as exc:
            return 1, f"Google Play nicht erreichbar ({type(exc).__name__})."
        finally:
            await self._close(client)

    async def _play_upload(
        self, client: httpx.AsyncClient, aab: Path, package: str
    ) -> tuple[int, str]:
        auth = {"Authorization": f"Bearer {await self._play_token(client)}"}
        app = f"{PLAY}/applications/{package}"
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
            f"{PLAY_UPLOAD}/applications/{package}/edits/{edit}/bundles",
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

    async def submit_play(self, package: str = PACKAGE) -> Report:
        """The newest App Bundle of the internal test into production, which sends it to
        Google's review."""
        report = Report()
        title = "Google Play: Produktion"
        if not self.vault.has("play"):
            report.add(title, "warn", "Kein Google-Play-Dienstkonto hinterlegt.")
            return report
        client = self._http()
        try:
            state, text = await self._submit_play(client, package)
            report.add(title, state, text)
        except StoreError as exc:
            report.add(title, "fail", str(exc))
        except httpx.HTTPError as exc:
            report.add(title, "fail", f"Google Play nicht erreichbar ({type(exc).__name__}).")
        finally:
            await self._close(client)
        return report

    async def _submit_play(self, client: httpx.AsyncClient, package: str) -> tuple[str, str]:
        auth = {"Authorization": f"Bearer {await self._play_token(client)}"}
        app = f"{PLAY}/applications/{package}"
        r = await client.post(f"{app}/edits", headers=auth)
        if r.status_code != 200:
            raise StoreError(f"Bearbeitung nicht möglich ({_message(r)}).")
        edit = str(_dict(r.json())["id"])
        r = await client.get(f"{app}/edits/{edit}/tracks/{TRACK}", headers=auth)
        if r.status_code != 200:
            raise StoreError(f"Interner Test nicht lesbar ({_message(r)}).")
        codes: set[int] = set()
        for release in _list(_dict(r.json()).get("releases")):
            raw = release.get("versionCodes")
            for c in cast(list[Any], raw) if isinstance(raw, list) else []:
                if str(c).isdigit():
                    codes.add(int(c))
        if not codes:
            raise StoreError("Im internen Test liegt noch keine Version; erst Release bauen.")
        code = str(max(codes))
        release: dict[str, Any] = {"versionCodes": [code], "status": "completed"}
        track = f"{app}/edits/{edit}/tracks/production"
        body = {"track": "production", "releases": [release]}
        r = await client.put(track, json=body, headers=auth)
        if r.status_code == 400 and "draft" in r.text.lower():
            # An app never published takes draft releases only.
            release["status"] = "draft"
            r = await client.put(track, json=body, headers=auth)
        if r.status_code != 200:
            raise StoreError(f"Produktion nicht gesetzt ({_message(r)}).")
        r = await client.post(f"{app}/edits/{edit}:commit", headers=auth)
        if r.status_code != 200:
            raise StoreError(f"Einreichen abgelehnt ({_message(r)}).")
        if release["status"] == "draft":
            return "warn", (
                f"Version {code} liegt als Entwurf in der Produktion. Die erste "
                "Veröffentlichung gibt Google nur in der Play Console frei (Produktion → "
                "Release prüfen → Einführen); vorher müssen dort alle Punkte unter „App "
                "einrichten“ erledigt sein."
            )
        return "ok", f"Version {code} ist eingereicht und geht nach Googles Prüfung live."

    # Store listing

    def screenshots(self, language: str | None = None, root: Path | None = None) -> list[Path]:
        """The uploaded phone screenshots, else the repository's (made from the app with
        sample data, app/test/store): the language's own (``<language>/screenshots``), else
        the shared ones. With ``root`` (a further app's listing) only those."""
        listing = root or self.listing
        uploaded = [] if root is not None else [self.screenshot_dir]
        own = [listing / language / "screenshots"] if language else []
        for folder in (*uploaded, *own, listing / "screenshots"):
            if folder.is_dir():
                found = sorted(p for p in folder.iterdir() if p.suffix in (".png", ".jpg"))
                if found:
                    return found
        return []

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
        for old in self.screenshot_dir.iterdir():  # never the repository's
            old.unlink()
        for name, data in named:
            (self.screenshot_dir / name).write_bytes(data)

    def listing_texts(self, language: str, root: Path | None = None) -> dict[str, str]:
        folder = (root or self.listing) / language
        return {
            "language": language,
            "title": (folder / "title.txt").read_text().strip(),
            "shortDescription": (folder / "short_description.txt").read_text().strip(),
            "fullDescription": (folder / "full_description.txt").read_text().strip(),
        }

    def start_listing(self, by: str, other: OtherApp | None = None) -> bool:
        """Push the listing in the background (it takes longer than the proxy waits for a
        page); False while one is still running. The result lands in ``listing.json``
        (``listing-<app>.json`` for a further app)."""
        name = f"listing-{other.key}.json" if other else "listing.json"
        task = self.jobs.get(name) if other else self._listing_task
        if task is not None and not task.done():
            return False
        self.vault.write_json(name, {"at": _now(), "by": by, "running": True})

        async def run() -> Report:
            try:
                report = await self.push_listing(other)
            except Exception:  # a bug, not Google: say so instead of "restarted"
                log.exception("store listing push failed")
                report = Report()
                report.add("Store-Eintrag", "fail", "Interner Fehler, siehe Log des Hubs.")
            steps = [{"title": x.title, "state": x.state, "text": x.text} for x in report.steps]
            self.vault.write_json(name, {"at": _now(), "by": by, "steps": steps})
            log.info(
                "store listing %s pushed by %s: %s",
                other.key if other else "allskyhub",
                by,
                [(x.title, x.state) for x in report.steps],
            )
            return report

        job = asyncio.create_task(run())
        if other:
            self.jobs[name] = job
        else:
            self._listing_task = job
        return True

    def listing_state(self, other: OtherApp | None = None) -> dict[str, Any] | None:
        """The last push: ``running``, or its ``steps`` (title, state, text)."""
        name = f"listing-{other.key}.json" if other else "listing.json"
        state = self.vault.read_json(name)
        task = self.jobs.get(name) if other else self._listing_task
        alive = task is not None and not task.done()
        if state and state.get("running") and not alive:  # the hub restarted meanwhile
            text = "Abgebrochen (der Hub wurde neu gestartet), bitte noch einmal übertragen."
            return {**state, "running": False, "steps": [
                {"title": "Store-Eintrag", "state": "fail", "text": text}
            ]}  # fmt: skip
        return state

    async def push_listing(self, other: OtherApp | None = None) -> Report:
        """Texts, icon, feature graphic and screenshots of every language to Play."""
        report = Report()
        if not self.vault.has("play"):
            report.add("Store-Eintrag", "warn", "Kein Google-Play-Dienstkonto hinterlegt.")
            return report
        client = self._client or httpx.AsyncClient(timeout=UPLOAD_TIMEOUT)
        try:
            await self._push_listing(client, report, other)
        except StoreError as exc:
            report.add("Store-Eintrag", "fail", str(exc))
        except httpx.HTTPError as exc:
            report.add(
                "Store-Eintrag", "fail", f"Google Play nicht erreichbar ({type(exc).__name__})."
            )
        finally:
            await self._close(client)
        return report

    async def fetch_listing(self, client: httpx.AsyncClient, app: OtherApp) -> Path:
        """A further app's ``store/`` from its repository's main branch, fresh."""
        r = await client.get(
            LISTING_TARBALL.format(repo=app.repo), timeout=UPLOAD_TIMEOUT, follow_redirects=True
        )
        if r.status_code != 200 or len(r.content) > LISTING_MAX_BYTES:
            raise StoreError(f"Store-Dateien von {app.repo} nicht ladbar ({r.status_code}).")
        dest = self.vault.root / "listings" / app.key
        return await asyncio.to_thread(extract_listing, r.content, dest)

    async def _push_listing(
        self, client: httpx.AsyncClient, report: Report, other: OtherApp | None = None
    ) -> None:
        auth = {"Authorization": f"Bearer {await self._play_token(client)}"}
        package = other.package if other else PACKAGE
        root = (await self.fetch_listing(client, other)) / "play" if other else self.listing
        email = other.contact_email if other else CONTACT_EMAIL
        website = other.website if other else CONTACT_WEBSITE
        app = f"{PLAY}/applications/{package}"
        r = await client.post(f"{app}/edits", headers=auth)
        if r.status_code == 404:
            raise StoreError(
                "Google kennt die App über die Schnittstelle noch nicht: erst das erste App "
                "Bundle von Hand hochladen."
            )
        if r.status_code != 200:
            raise StoreError(f"Bearbeitung nicht möglich ({_message(r)}).")
        edit = f"{app}/edits/{_dict(r.json())['id']}"
        upload = f"{PLAY_UPLOAD}/applications/{package}/edits/{_dict(r.json())['id']}"
        details = {
            "defaultLanguage": LANGUAGES[0],
            "contactEmail": email,
            "contactWebsite": website,
        }
        r = await client.put(f"{edit}/details", json=details, headers=auth)
        if r.status_code != 200:
            raise StoreError(f"Kontaktdaten abgelehnt ({_message(r)}).")
        for language in LANGUAGES:
            shots = self.screenshots(language, root if other else None)
            texts = self.listing_texts(language, root)
            r = await client.put(f"{edit}/listings/{language}", json=texts, headers=auth)
            if r.status_code != 200:
                raise StoreError(f"Texte {language} abgelehnt ({_message(r)}).")
            images: list[tuple[str, Path]] = [
                ("icon", root / "icon.png"),
                ("featureGraphic", root / language / "feature.png"),
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
        if any(
            len(self.screenshots(lang, root if other else None)) < SCREENSHOTS[0]
            for lang in LANGUAGES
        ):
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
