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
import secrets
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

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


# -- shared per-run secret ---------------------------------------------------
#
# Every local control plane (agent, combat, sentinel, C# bridge, relay admin
# routes) requires the same bearer token. It is not a login: the host mints 32
# random bytes once per run and writes them to a per-user file, and every
# component reads that file. No prompt, no password, no user action.
#
# `DOFUS_RUN_TOKEN` wins when it is set, because that is what run_brain.py and
# net/orchestrator.py already export to their children. Otherwise the file is
# authoritative, and the first process to need it creates it atomically.
#
# That name is for *authorisation only*. The network pipeline's provenance token is
# `DOFUS_SESSION_TOKEN` (net/orchestrator.SESSION_TOKEN_ENV) precisely so it cannot land
# here: exporting it as `DOFUS_RUN_TOKEN` made every Python service present a bearer the
# host had never minted, because this function reads the environment before the file.

SECRET_FILENAME = "local_secret"


def _secret_path() -> Path:
    return state_dir() / SECRET_FILENAME


def ensure_local_secret() -> str:
    """Return this run's secret, creating the per-user file if it is absent."""
    env = os.environ.get("DOFUS_RUN_TOKEN", "").strip()
    if env:
        return env
    path = _secret_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    candidate = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL makes two processes starting at the same time agree on one
        # value: the loser reads what the winner wrote instead of overwriting.
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, candidate.encode("ascii"))
        finally:
            os.close(fd)
        return candidate
    except FileExistsError:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    except OSError:
        return ""


def local_secret() -> str:
    """The secret every loopback caller must present, or "" when unavailable.

    Read on every call rather than cached at import: the host can create the
    file after a service has already started, and a stale cached value would
    lock that service out of its own run.
    """
    return ensure_local_secret()


def local_headers() -> dict:
    """Authorization header for a loopback call, or {} when no secret exists."""
    secret = local_secret()
    return {"Authorization": "Bearer " + secret} if secret else {}


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
        request = urllib.request.Request(base_url(host), headers=local_headers())
        local_opener().open(request, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True     # it answered, even if with 404 — somebody is home
    except Exception:   # noqa: BLE001 - timeout, refused, reset all mean "not there"
        return False


def client_count(host: str = "127.0.0.1", timeout: float = 2.0) -> Optional[int]:
    """How many client windows the host has open, or None if it did not answer.

    `GET /agent/health` is the only read that costs no game packet: the bridge counts
    `GetActiveInstanceIds()` itself. A directive is not an alternative — with no window
    the bridge answers "no client instance is running" without reaching a page, which is
    how a live run spent 27 steps learning that the host had none open.
    """
    import json
    import urllib.error
    url = "http://%s:%d/agent/health" % (host, bridge_port())
    request = urllib.request.Request(url, headers=local_headers())
    try:
        with local_opener().open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:   # noqa: BLE001 - any failure means "unknown", never "zero"
        return None
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return None
    count = payload.get("instances")
    return count if isinstance(count, int) and count >= 0 else None
