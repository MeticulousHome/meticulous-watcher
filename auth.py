"""Request authorization for the watcher's HTTP surface.

The watcher serves journal logs, service status and report archives -- all
diagnostic data that must not be readable by arbitrary LAN peers. It applies
the same boundary as the machine backend's local API:

  * loopback callers (the backend/Dial on the machine itself) are exempt;
  * a LAN client (which reaches us through nginx, with X-Real-IP set) must
    present a valid per-device pairing token, either as an
    `Authorization: Bearer` header or in the `met_device_token` cookie the
    pairing pages store.

Tokens are minted and persisted by the backend (see meticulous-backend
pairing.py): config.yml keeps only SHA-256 hashes of them under the
paired_devices section. The watcher verifies against those hashes directly, so
it needs no IPC with the backend. The hashes are extracted with a targeted
scan for `token_hash: <64 hex>` lines instead of a YAML parser, so no new
dependency is added to the watcher for this; the file is rescanned only when
its mtime changes.

The X-Real-IP trust matches the backend: nginx overwrites the header
unconditionally for proxied locations, so a LAN client cannot forge loopback.
"""

import hashlib
import hmac
import os
import re

CONFIG_FILE = os.getenv("CONFIG_PATH", "/meticulous-user/config") + "/config.yml"

TOKEN_COOKIE_NAME = "met_device_token"

_LOOPBACK = ("127.0.0.1", "::1", "localhost")

_TOKEN_HASH_RE = re.compile(r"token_hash:\s*['\"]?([0-9a-f]{64})['\"]?")

_cache = {"mtime": None, "hashes": frozenset()}


def _token_hashes():
    """Current paired-device token hashes, re-read when config.yml changes."""
    try:
        mtime = os.path.getmtime(CONFIG_FILE)
    except OSError:
        return frozenset()
    if _cache["mtime"] != mtime:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            _cache["hashes"] = frozenset(_TOKEN_HASH_RE.findall(content))
            _cache["mtime"] = mtime
        except OSError:
            return frozenset()
    return _cache["hashes"]


def _is_loopback(value):
    if not value:
        return False
    return value in _LOOPBACK or value.startswith("127.")


def _client_is_local(remote_ip, x_real_ip):
    if x_real_ip and not _is_loopback(x_real_ip):
        return False
    return _is_loopback(remote_ip)


def _extract_token(authorization, cookie_header):
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    if cookie_header:
        for part in cookie_header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == TOKEN_COOKIE_NAME and value:
                return value.strip()
    return None


def _verify_token(token):
    if not token:
        return False
    candidate = hashlib.sha256(token.encode("utf-8")).hexdigest()
    result = False
    # Compare against every stored hash so timing does not reveal which (if
    # any) entry matched.
    for stored in _token_hashes():
        if hmac.compare_digest(stored, candidate):
            result = True
    return result


def request_is_authorized(handler) -> bool:
    """The authorization decision for a watcher request."""
    if handler.request.method == "OPTIONS":
        return True
    if _client_is_local(
        handler.request.remote_ip, handler.request.headers.get("X-Real-IP")
    ):
        return True
    token = _extract_token(
        handler.request.headers.get("Authorization"),
        handler.request.headers.get("Cookie"),
    )
    return _verify_token(token)


class AuthMixin:
    """Prepend to a RequestHandler so unauthorized requests get a 401 with an
    explanation of how to authorize, before any handler logic runs."""

    def prepare(self):
        if not request_is_authorized(self):
            self.set_status(401)
            self.set_header("Content-Type", "application/json")
            self.finish(
                '{"error": "Unauthorized", "detail": "This device is not '
                'authorized for machine diagnostics. Open the machine\'s page '
                'and authorize this device first."}'
            )
            return None
        return super().prepare()
