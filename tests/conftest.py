"""Platform-safe test imports for the Linux-only systemd binding."""

import sys
import types

try:
    import systemd  # noqa: F401
except ModuleNotFoundError:
    systemd = types.ModuleType("systemd")
    systemd.journal = types.SimpleNamespace(LOG_DEBUG=7, Reader=object)
    sys.modules["systemd"] = systemd
