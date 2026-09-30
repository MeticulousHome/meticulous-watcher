"""Request authorization for the watcher's HTTP surface.

The watcher serves journal logs, service status and report archives -- all
diagnostic data that must not be readable by arbitrary LAN peers. It applies
the same boundary as the machine backend's local API:

  * loopback callers (the backend/Dial on the machine itself) are exempt;
  * a LAN client (which reaches us through nginx, with X-Real-IP set) must
    present a valid per-device pairing token as an `Authorization: Bearer`
    header. The met_device_token cookie is NOT accepted (it was removed from
    the backend too, ADV-020 / identity D10: a cookie rides plain navigations
    with no identity check).

Tokens are minted and persisted by the backend (see meticulous-backend
pairing.py): config.yml keeps only SHA-256 hashes of them under the
paired_devices section. The watcher verifies against those hashes directly, so
it needs no IPC with the backend. The hashes are extracted from the
`paired_devices` block ONLY, scoped by indentation (ADV-019: a `token_hash`
field appearing anywhere else in config.yml must never become a credential).
PyYAML is deliberately not a watcher dependency, so this is a small structured
scan rather than a full parse; config.yml is re-read only when its mtime
changes.

The X-Real-IP trust matches the backend: nginx overwrites the header
unconditionally for proxied locations, so a LAN client cannot forge loopback.
"""

import hashlib
import hmac
import os
import re

CONFIG_FILE = os.getenv("CONFIG_PATH", "/meticulous-user/config") + "/config.yml"

_LOOPBACK = ("127.0.0.1", "::1", "localhost")

# token_hash line inside the paired_devices block, indented deeper than a
# device id (device ids sit at 2 spaces, their attributes at 4).
_TOKEN_HASH_LINE = re.compile(r"^(\s{3,})token_hash:\s*['\"]?([0-9a-f]{64})['\"]?\s*$")

_cache = {"mtime": None, "hashes": frozenset()}


def _paired_device_hashes(text):
    """Token hashes taken ONLY from the top-level `paired_devices` block.

    A `token_hash: <64 hex>` appearing under any other key (or injected as an
    unrelated field) is ignored, so it can never become a valid credential
    (ADV-019). The block is delimited by indentation: it starts at the
    `paired_devices:` top-level key and ends at the next column-0 key.
    """
    hashes = set()
    in_block = False
    for line in text.splitlines():
        if not in_block:
            if re.match(r"^paired_devices:\s*$", line):
                in_block = True  # block mapping follows on indented lines
            # `paired_devices: {}` (or any inline form) has no block -> no devices
            continue
        # A non-indented, non-blank, non-comment line ends the block.
        stripped = line.strip()
        if stripped and not line[0].isspace() and not stripped.startswith("#"):
            break
        m = _TOKEN_HASH_LINE.match(line)
        if m:
            hashes.add(m.group(2))
    return frozenset(hashes)


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
            _cache["hashes"] = _paired_device_hashes(content)
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


def _extract_token(authorization):
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
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
    token = _extract_token(handler.request.headers.get("Authorization"))
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
