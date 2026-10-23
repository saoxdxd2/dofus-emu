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
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8893

# A direct opener, for every call to this machine.
#
# `urllib.request.urlopen` honours HTTP_PROXY from the environment, and on a machine
# running a VPN or a local proxy client that variable is set globally — this one had
# HTTP_PROXY=http://127.0.0.1:20056. Every directive the agent sent to
# http://127.0.0.1:8894/directive therefore went *through that proxy* instead of
# straight to the bridge: an extra hop for a loopback call, a second failure point,
# and a 502 when the proxy could not reach a port that was only listening locally.
# A loopback address must never be proxied. One opener, used by every local call.
_NO_PROXY = urllib.request.ProxyHandler({})
_LOCAL_OPENER = urllib.request.build_opener(_NO_PROXY)
# The default opener: honours HTTP_PROXY from the environment. Built once so both
# branches return the same kind of object (something with `.open`).
_ENV_OPENER = urllib.request.build_opener()


def local_opener() -> urllib.request.OpenerDirector:
    """An opener that ignores HTTP_PROXY/HTTPS_PROXY. Use it for 127.0.0.1."""
    return _LOCAL_OPENER


def opener_for(url: str) -> urllib.request.OpenerDirector:
    """Direct opener for a loopback URL, the environment's opener otherwise."""
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return _LOCAL_OPENER
    return _ENV_OPENER


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
    try:
        # Local opener: HTTP_PROXY must not intercept a loopback probe.
        local_opener().open(base_url(host), timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True     # it answered, even if with 404 — somebody is home
    except Exception:   # noqa: BLE001 - timeout, refused, reset all mean "not there"
        return False
