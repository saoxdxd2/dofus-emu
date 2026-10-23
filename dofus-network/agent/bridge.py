"""Where the client bridge actually is.

The bridge binds a port at startup and publishes it to `bridge_port.txt`. Hardcoding
8893 on this side was a real outage: something on the machine held 8893, the bridge
fell back (or failed), and every directive went to a port nobody was serving. The
symptom was "the app is running but the AI does nothing" — the loop took one step,
every call failed, and it exited looking like it had finished.

So: ask the bridge where it is, fall back to the documented default only when there is
no answer. One resolver, used by every caller, because three copies of a default port
is three chances to disagree.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PORT = 8893


def state_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm"


def bridge_port() -> int:
    """The port the bridge published, or the default if it has not said."""
    path = state_dir() / "bridge_port.txt"
    try:
        text = path.read_text(encoding="utf-8").strip()
        port = int(text)
        return port if 0 < port < 65536 else DEFAULT_PORT
    except (OSError, ValueError):
        return DEFAULT_PORT


def directive_url(host: str = "127.0.0.1") -> str:
    return "http://%s:%d/directive" % (host, bridge_port())


def base_url(host: str = "127.0.0.1") -> str:
    return "http://%s:%d/" % (host, bridge_port())


def reachable(host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
    """Is anything *answering* at the bridge's advertised port?

    A TCP connect is not the test. A host that was force-killed leaves a listening
    socket the kernel keeps accepting on, and it never replies — so `create_connection`
    succeeds against a bridge that does not exist, which is the exact failure that made
    the loop look alive while every directive timed out. Demand a response.
    """
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(base_url(host), timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True     # it answered, even if with 404 — somebody is home
    except Exception:   # noqa: BLE001 - timeout, refused, reset all mean "not there"
        return False
