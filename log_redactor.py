"""Redaction rules for machine_logs.txt before it leaves the device.

Scope: the must-redact set only (SSID, BSSID/MACs, IPv6, root_password,
APPassword). Tier-2 identifiers (serial, hostname, hawkbit ID) are
deliberately NOT touched -- support needs them, and they are covered by
retention policy rather than by this filter.

Rule order is load-bearing; see RULES below.
"""

import hashlib
import hmac
import os
import re
from pathlib import Path

REDACTED = "[REDACTED]"
DEFAULT_KEY_PATH = "/root/.redaction_key"
KEY_SIZE = 32

# MACs that identify no one -- keep them, they carry diagnostic signal.
MAC_ALLOWLIST = re.compile(
    r"^(?:ff:ff:ff:ff:ff:ff|00:00:00:00:00:00|33:33:[0-9a-f:]+|01:00:5e:[0-9a-f:]+)$",
    re.IGNORECASE,
)

# An SSID shorter than this is not literal-swept in pass 2 -- too likely to
# collide with ordinary log text ("up", "ok"). Anchored hits still redact.
MIN_SWEEP_LEN = 4

# NetworkManager logs non-wifi connections with the same "connection '<name>'"
# wording as wifi ones. These are interface names, not user network names.
# Anything NOT on this list is still redacted -- over-redaction is the safe
# direction for a field that holds arbitrary user text.
CONNECTION_ALLOWLIST = re.compile(
    r"^(?:lo|usb\d+|eth\d+|Wired connection \d+)$", re.IGNORECASE
)


def _pseudonym(kind, value, key):
    """Stable per-device pseudonym.

    Same value -> same token within one device's reports, so an engineer can
    still see "the MAC changed at this boot" or "same AP as last report".
    Different device -> different token, so reports cannot be joined across
    the fleet. Never used for credentials.
    """
    digest = hmac.new(key, f"{kind}\0{value}".encode(), hashlib.sha256)
    # Underscore, NOT colon. "[SSID:abcd1234]" is itself a key:value shape and
    # the unquoted `ssid:` rule below happily matched inside it, learning the
    # hash as if it were a network name. An underscore keeps \bssid\b from
    # ever landing next to a separator.
    return f"[{kind}_{digest.hexdigest()[:8]}]"


# --------------------------------------------------------------------------
# Rule 1 -- credentials.  Runs FIRST so a password that happens to look like
# a MAC or an SSID is destroyed outright rather than pseudonymised.
# --------------------------------------------------------------------------

# Key must sit immediately against the separator. This is what stops the rule
# eating "Password already set: True" and "password changed for root".
_CRED_KEY = (
    r"root_?password|ap_?password|passwd|password|passphrase"
    r"|psk|pre_?shared_?key|secret|token|api[_-]?key|authorization|bearer"
)
# The [REDACTED]/token alternative must come first: the bare-token branch
# stops at "]", so without it a second pass would emit "[REDACTED]]".
_CRED_VALUE = (
    r"""(?P<val>\[REDACTED\]|\[(?:SSID|MAC|IPV6)_[0-9a-f]{8}\]"""
    r"""|'[^'\n]*'|"[^"\n]*"|[^\s,;}\]]+)"""
)

# The optional quotes around the key are what let this match the JSON shape
# {"root_password": "abc"} as well as the YAML shape root_password: abc.
RE_CRED_KV = re.compile(
    rf"(?P<key>[\"']?\b(?:{_CRED_KEY})\b[\"']?)(?P<sep>\s*[:=]\s*){_CRED_VALUE}",
    re.IGNORECASE,
)

# NetworkManager's own shape: Config: added 'psk' value '<hidden>'
RE_CRED_NM = re.compile(
    rf"(?P<key>added\s+'(?:{_CRED_KEY})'\s+value\s+)(?P<val>'[^'\n]*')",
    re.IGNORECASE,
)


def _sub_cred(m):
    val = m.group("val")
    if RE_ALREADY_DONE.match(val.strip("'\"")):
        return m.group(0)
    quote = val[0] if val[:1] in ("'", '"') else ""
    return f"{m.group('key')}{m.groupdict().get('sep', '')}{quote}{REDACTED}{quote}"


# --------------------------------------------------------------------------
# Rule 2 -- MAC addresses (own NIC, randomised scan MAC, and the BSSID).
# Runs BEFORE IPv6: a MAC also matches a loose IPv6 pattern.
# --------------------------------------------------------------------------
RE_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")


# --------------------------------------------------------------------------
# Rule 3 -- IPv6.
# Both forms require either "::" or a full 8 groups. A naive
# (?:[0-9a-f]{1,4}:){2,7} pattern matches the "01:42:05" in every timestamp
# in this file -- do not use one.
# --------------------------------------------------------------------------
RE_IPV6 = re.compile(
    r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b"  # full form
    # Leading compression, including the two allowlisted values "::" and
    # "::1". This branch is needed because the general compressed branch
    # below requires at least one group before "::".
    r"|(?<![:\w])::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?(?![:\w])"
    r"|(?<![:\w])(?:[0-9A-Fa-f]{1,4}:){1,7}:"
    r"(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?(?![:\w])"
)
# Loopback and unspecified are not identifying.
IPV6_ALLOWLIST = {"::1", "::"}


# --------------------------------------------------------------------------
# Rule 4 -- SSID, anchored on context.
# An SSID is arbitrary user text, so it cannot be matched by shape. Each
# pattern below anchors on the surrounding message and captures group "val".
# Every value captured here is also LEARNED and swept globally in pass 2,
# which is what catches message shapes not enumerated here.
# --------------------------------------------------------------------------
RE_SSID = [
    re.compile(r"(?P<pre>Config: added 'ssid' value ')(?P<val>[^'\n]{1,32})(?P<post>')"),
    re.compile(r"(?P<pre>SSID=')(?P<val>[^'\n]{1,32})(?P<post>')"),
    re.compile(r"(?P<pre>associate with SSID ')(?P<val>[^'\n]{1,32})(?P<post>')"),
    re.compile(r"(?P<pre>wireless network \")(?P<val>[^\"\n]{1,32})(?P<post>\")"),
    re.compile(r"(?P<pre>\baccess point ')(?P<val>[^'\n]{1,32})(?P<post>')"),
    re.compile(r"(?P<pre>\bconnection ')(?P<val>[^'\n]{1,32})(?P<post>')"),
    re.compile(r"(?P<pre>policy: set ')(?P<val>[^'\n]{1,32})(?P<post>')"),
    re.compile(r"(?P<pre>\bssid\s*[:=]\s*[\"'])(?P<val>[^\"'\n]{1,32})(?P<post>[\"'])", re.I),
    # unquoted YAML/JSON form: ssid: HomeNet
    re.compile(
        r"(?P<pre>\bssid\s*[:=]\s*)"
        r"(?P<val>[^\s\"'#,;}\]][^\n#,;}\]]{0,31})(?P<post>)",
        re.I,
    ),
]

# Rules must be idempotent: several patterns overlap, and a value that has
# already been replaced must not be re-processed (doing so silently ate the
# surrounding quotes on SSID='...' lines).
RE_ALREADY_DONE = re.compile(r"^(?:\[(?:SSID|MAC|IPV6)_[0-9a-f]{8}\]|\[REDACTED\])$")

# Rule 5 -- the backend config dump embeds a YAML map whose *keys* are every
# network the machine has ever joined:
#     config DEBUG CONF:   wifi:
#     config DEBUG CONF:     KnownWifis:
#     config DEBUG CONF:       HomeNet:
# Empty ({}) in this sample, so no regex above would ever have caught it.
# Needs indentation tracking, not a line pattern.
RE_CONF_LINE = re.compile(r"^(?P<pre>.*?CONF:)(?P<yaml>.*)$")
RE_KNOWN_WIFIS = re.compile(r"^(?P<indent>\s*)KnownWifis:\s*(?P<inline>\S*)\s*$")
RE_YAML_KEY = re.compile(r"^(?P<indent>\s*)(?P<key>[^\s:#][^:\n]*?)(?P<rest>:.*)$")


def redact(text, key):
    """Two passes: anchored rules (which learn SSIDs), then a literal sweep."""
    learned_ssids = set()

    # ---------------- pass 1 ----------------
    out_lines = []
    known_wifis_indent = None
    ssid_key_indent = None  # exact indent of the SSID keys, one level in

    for line in text.splitlines(keepends=True):
        line = RE_CRED_KV.sub(_sub_cred, line)
        line = RE_CRED_NM.sub(_sub_cred, line)

        line = RE_MAC.sub(
            lambda m: m.group(0)
            if MAC_ALLOWLIST.match(m.group(0))
            else _pseudonym("MAC", m.group(0).lower(), key),
            line,
        )

        line = RE_IPV6.sub(
            lambda m: m.group(0)
            if m.group(0) in IPV6_ALLOWLIST
            else _pseudonym("IPV6", m.group(0).lower(), key),
            line,
        )

        for pattern in RE_SSID:

            def _sub_ssid(m):
                val = m.group("val").strip("'\"")
                if (
                    not val
                    or CONNECTION_ALLOWLIST.match(val)
                    or RE_ALREADY_DONE.match(val)
                ):
                    return m.group(0)
                learned_ssids.add(val)
                token = _pseudonym("SSID", val, key)
                pre, post = m.group("pre"), m.groupdict().get("post", "")
                return f"{pre}{token}{post}"

            line = pattern.sub(_sub_ssid, line)

        # KnownWifis block: keys are SSIDs.
        if line.endswith("\r\n"):
            line_body, line_ending = line[:-2], "\r\n"
        elif line.endswith(("\n", "\r")):
            line_body, line_ending = line[:-1], line[-1]
        else:
            line_body, line_ending = line, ""

        conf = RE_CONF_LINE.match(line_body)
        if conf:
            yaml_part = conf.group("yaml")
            if known_wifis_indent is not None:
                km = RE_YAML_KEY.match(yaml_part)
                indent = len(km.group("indent")) if km else None
                if km and indent > known_wifis_indent:
                    # First deeper key fixes the SSID level. Anything deeper
                    # than that is a per-network attribute (password,
                    # last_used) -- not an SSID, and handled by rule 1.
                    # This must happen even for an already-redacted key, or a
                    # second pass over redacted output locks onto the
                    # attribute level and pseudonymises "password" as an SSID.
                    if ssid_key_indent is None:
                        ssid_key_indent = indent
                    val = km.group("key").strip()
                    if indent == ssid_key_indent and not RE_ALREADY_DONE.match(val):
                        learned_ssids.add(val)
                        line = (
                            conf.group("pre")
                            + km.group("indent")
                            + _pseudonym("SSID", val, key)
                            + km.group("rest")
                            + line_ending
                        )
                elif yaml_part.strip():
                    known_wifis_indent = None
                    ssid_key_indent = None
            kw = RE_KNOWN_WIFIS.match(yaml_part)
            if kw and kw.group("inline") in ("", "|", ">"):
                known_wifis_indent = len(kw.group("indent"))
                ssid_key_indent = None

        out_lines.append(line)

    text = "".join(out_lines)

    # ---------------- pass 2: literal sweep ----------------
    # Catches the SSID wherever it appears in a message shape pass 1 does not
    # know about. Longest-first so a substring SSID cannot shadow a longer one.
    for ssid in sorted(learned_ssids, key=len, reverse=True):
        if len(ssid) < MIN_SWEEP_LEN or RE_ALREADY_DONE.match(ssid):
            continue
        text = re.sub(
            rf"(?<![\w-]){re.escape(ssid)}(?![\w-])",
            _pseudonym("SSID", ssid, key),
            text,
        )

    return text, learned_ssids


def _read_key(path):
    with open(path, "rb") as fh:
        key = fh.read()
    if len(key) != KEY_SIZE:
        raise ValueError(f"redaction key at {path!r} must be exactly {KEY_SIZE} bytes")
    return key


def load_key(path=DEFAULT_KEY_PATH):
    """Per-device key. Not a fleet secret: compromise of one device's key
    reveals nothing about any other device's reports."""

    import time

    try:
        key = _read_key(path)
        os.chmod(path, 0o600)
        return key
    except FileNotFoundError:
        pass

    key = os.urandom(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another request won the first-use race. Keep its key so tokens stay
        # stable across concurrent reports.
        # wait 0.1s to make sure the file is not being actively written and thus the key be incomplete
        time.sleep(0.1)
        key = _read_key(path)
        os.chmod(path, 0o600)
        return key

    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
        fh.flush()
        os.fsync(fh.fileno())
    return key

if __name__ == "__main__":
    import sys

    src = sys.argv[1]
    key = os.environ.get("REDACTION_KEY", "test-key-not-for-production").encode()
    with open(src, encoding="utf-8", errors="replace") as fh:
        out, ssids = redact(fh.read(), key)
    sys.stdout.write(out)
    print(f"[learned {len(ssids)} SSID(s)]", file=sys.stderr)
