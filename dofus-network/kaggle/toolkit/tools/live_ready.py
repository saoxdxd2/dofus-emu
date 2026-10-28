"""Preflight the host before a live Full Auto run.

The checks are the states that have actually cost runs:
- a leftover `AUTOPILOT_STOP` ends the next session on its first step;
- a client that was not fully restarted keeps the previous injected page, and every fix
  after that looks absent (the last recorded observation carries `agent_build`);
- the installer can predate the page it should ship;
- the bridge/client window and the combat brain may be down (warn: the loop waits or the
  page fallback fights);
- the knowledge store and the observed-quest file change what the run can resolve
  (info).

FAIL exits 1; WARN/INFO do not. Nothing here writes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import socket
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.autopilot import DEFAULT_LOG, DEFAULT_STOP  # noqa: E402
from agent.bridge import client_count, reachable  # noqa: E402
from agent.knowledge_context import DEFAULT_STORE, OBSERVED_FILE  # noqa: E402
from agent.command_shadow import DEFAULT_MODEL as COMMAND_SHADOW_MODEL  # noqa: E402

PAGE = ROOT.parent / "scripts" / "mobile-disguise.js"
SETUP = ROOT.parent / "setup.exe"
BRAIN_URL = "http://127.0.0.1:8891/health"


def _entry(level: str, name: str, detail: str) -> Dict[str, str]:
    return {"level": level, "name": name, "detail": detail}


def page_build(path: Path = PAGE) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    marker = "var AGENT_BUILD = '"
    at = text.find(marker)
    if at < 0:
        return None
    rest = text[at + len(marker):]
    end = rest.find("'")
    return rest[:end] if end > 0 else None


def _tail_lines(path: Path, max_bytes: int = 400_000) -> List[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()      # drop the partial first line
            data = handle.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def last_log_build(path: Path = DEFAULT_LOG) -> Optional[str]:
    for line in reversed(_tail_lines(path)):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        for key in ("observation", "obs_before", "obs_after", "obs", "snapshot"):
            obs = row.get(key)
            if isinstance(obs, dict) and obs.get("agent_build"):
                return str(obs["agent_build"])
    return None


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def brain_up(url: str = BRAIN_URL, timeout: float = 1.0) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return 200 <= int(getattr(response, "status", 0)) < 300
    except Exception:      # noqa: BLE001 - a down brain is a warning, not a crash
        return False


NETWORK_INI = ROOT.parent / "network.ini"
PROXIES_FILE = ROOT.parent / "proxies.txt"
# The bundle the client itself cannot do without. When this request fails the page shows its
# generic "Server busy" alert in a *native* dialog, which freezes the injected agent completely.
LOGIN_ASSET = "https://dt-proxy-production-login.ankama-games.com/build/primus.js"


def network_config(path: Path = NETWORK_INI) -> Dict[str, str]:
    """`network.ini` as keys — the host's own file, read rather than assumed."""
    out: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip().lower()] = value.strip()
    return out


def _endpoint(url: str) -> Optional[Tuple[str, int]]:
    """host:port of a `socks5://h:p` / `http://h:p` upstream, or None when it is not one."""
    import urllib.parse
    if not url or "://" not in url:
        return None
    parsed = urllib.parse.urlparse(url)
    try:
        port = int(parsed.port)
    except (TypeError, ValueError):
        return None
    return parsed.hostname, port


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def egress(config: Optional[Dict[str, str]] = None,
           relay_probe: bool = True,
           pool_path: Path = PROXIES_FILE) -> Dict[str, str]:
    """Is there any path from this machine to Ankama that the client will actually use?

    Measured the hard way on 2026-10-27: `mode=custom` with `upstream=socks5://127.0.0.1:40000`
    while nothing listened on 40000, and `fail_closed=1` (correctly) refusing direct. Every request
    502'd, six instances each raised a blocking "Server busy" alert, and no preflight said a word,
    because nothing in the tooling had ever read `network.ini`. A launch into a dead egress is not
    a test — it is six modal dialogs waiting to be clicked.
    """
    cfg = config if config is not None else network_config()
    mode = (cfg.get("mode") or "auto").lower()
    if mode == "direct":
        return _entry("INFO", "egress", "mode=direct: the real IP is what Ankama sees (by choice)")

    upstream = cfg.get("upstream") or ""
    endpoint = _endpoint(upstream)
    if endpoint is None and mode in ("wireproxy", "warp"):
        port = cfg.get("socks_port") or "40000"
        endpoint = ("127.0.0.1", int(port))
    if endpoint is None and mode == "custom":
        # custom with an empty upstream means round-robin proxies.txt
        pool = [line.strip() for line in pool_path.read_text(encoding="utf-8",
                                                              errors="replace").splitlines()
                if line.strip() and not line.startswith("#")] if pool_path.is_file() else []
        live = [_endpoint(p) for p in pool[:12]]
        live = [e for e in live if e and _port_open(e[0], e[1], 0.6)]
        if not live:
            return _entry("FAIL", "egress",
                          "mode=custom with no upstream and %d pool entries, none of which "
                          "accepts a connection; the client would 502 on primus.js and raise "
                          "its blocking 'Server busy' alert" % len(pool))
        return _entry("PASS", "egress",
                      "pool: %d of the first %d entries accept connections" % (len(live),
                                                                               min(12, len(pool))))
    if endpoint is None:
        return _entry("WARN", "egress", "mode=%s but no upstream to probe" % mode)

    host, port = endpoint
    if not _port_open(host, port, 1.2):
        return _entry("FAIL", "egress",
                      "upstream %s:%d is not listening (mode=%s, fail_closed=%s). Nothing can "
                      "reach Ankama: every asset request 502s and the page answers with a native "
                      "'Server busy' dialog that blocks the agent. Start the tunnel, or set "
                      "mode=auto, before launching an instance."
                      % (host, port, mode, cfg.get("fail_closed", "1")))
    if relay_probe:
        base = int(cfg.get("base_port") or 8880)
        open_relays = [base + slot for slot in range(1, 8)
                       if _port_open("127.0.0.1", base + slot, 0.3)]
        if not open_relays:
            return _entry("PASS", "egress",
                          "upstream %s:%d accepts connections; no relay port is up yet (a slot "
                          "starts one on base_port+N)" % (host, port))
        for relay in open_relays:
            if _fetch_via(LOGIN_ASSET, "http://127.0.0.1:%d" % relay):
                return _entry("PASS", "egress",
                              "login bundle fetched through relay %d (upstream %s:%d)"
                              % (relay, host, port))
        return _entry("FAIL", "egress",
                      "relay %s answers but the login bundle request fails through it; the page "
                      "will show 'Server busy' (upstream %s:%d is open but not carrying traffic)"
                      % (open_relays, host, port))
    return _entry("PASS", "egress", "upstream %s:%d accepts connections" % (host, port))


def _fetch_via(url: str, proxy: str, timeout: float = 8.0) -> bool:
    """One GET through the relay's HTTP proxy — the same path the client uses."""
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        with opener.open(url, timeout=timeout) as response:
            return 200 <= int(getattr(response, "status", 0)) < 300
    except Exception:      # noqa: BLE001 - an unreachable asset is the finding, not a crash
        return False


def checks(log: Path = DEFAULT_LOG, stop: Path = DEFAULT_STOP,
           page: Path = PAGE, setup: Path = SETUP,
           store: Path = DEFAULT_STORE, observed: Path = OBSERVED_FILE,
           command_model: Path = COMMAND_SHADOW_MODEL,
           bridge: Callable[[], bool] = reachable,
           clients: Callable[[], Optional[int]] = client_count,
           brain: Callable[[], bool] = brain_up,
           egress_entry: Callable[[], Dict[str, str]] = None) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []

    if stop.exists():
        out.append(_entry("FAIL", "stop file",
                          "%s exists; the next run would end on its first step "
                          "(remove it deliberately)" % stop))
    else:
        out.append(_entry("PASS", "stop file", "absent"))

    current = page_build(page)
    last = last_log_build(log)
    if not current:
        out.append(_entry("FAIL", "page build", "no AGENT_BUILD in %s" % page))
    elif last is None:
        out.append(_entry("INFO", "page build",
                          "repo %s; no recorded client build yet" % current))
    elif last == current:
        out.append(_entry("PASS", "page build", "client and repo both %s" % current))
    else:
        out.append(_entry("FAIL", "page build",
                          "last client recorded %s but the repo is %s; restart the app "
                          "fully (a reload keeps the old page)" % (last, current)))

    page_time, setup_time = _mtime(page), _mtime(setup)
    if setup_time is None:
        out.append(_entry("WARN", "installer", "setup.exe is absent; run compile.bat"))
    elif page_time and setup_time < page_time:
        out.append(_entry("WARN", "installer",
                          "setup.exe is older than the page; rebuild before installing"))
    else:
        out.append(_entry("PASS", "installer", "setup.exe is not older than the page"))

    # injectable like every other probe here: a unit test must not depend on this machine's
    # tunnel, and a live run must not depend on a test fixture
    out.append((egress_entry or egress)())

    try:
        up = bool(bridge())
    except Exception:      # noqa: BLE001
        up = False
    if up:
        count = clients()
        if count is None:
            out.append(_entry("WARN", "client", "bridge answers but the window count is unknown"))
        elif count <= 0:
            out.append(_entry("WARN", "client", "bridge answers; no client window yet"))
        else:
            out.append(_entry("PASS", "client", "%d window(s); bridge reachable" % count))
    else:
        out.append(_entry("WARN", "client", "bridge is not reachable; start DofusFarm"))

    out.append(_entry("PASS", "brain", "combat brain answers /health")
               if brain() else
               _entry("WARN", "brain", "combat brain is down; the page fallback will fight"))

    out.append(_entry("PASS", "knowledge store", str(store)) if store.is_file()
               else _entry("WARN", "knowledge store", "%s is absent" % store))
    if observed.is_file():
        out.append(_entry("INFO", "observed quests", str(observed)))
    else:
        out.append(_entry("INFO", "observed quests",
                          "none yet; the first live run populates it (ingest tool)"))
    onnx_config = command_model.with_name("command_onnx.json")
    if onnx_config.is_file():
        try:
            config = json.loads(onnx_config.read_text(encoding="utf-8"))
            root = onnx_config.parent
            refs = [root / config["onnx"], root / config["tokenizer"]]
            absent = [path.name for path in refs if not path.is_file()]
            deps = [name for name in ("onnxruntime", "tokenizers", "numpy")
                    if importlib.util.find_spec(name) is None]
            if absent or deps:
                out.append(_entry("INFO", "command shadow",
                                  "onnx config present; missing %s"
                                  % ", ".join(absent + deps)))
            else:
                out.append(_entry("PASS", "command shadow",
                                  "onnx %s ready; opt in with --command-shadow auto"
                                  % config["onnx"]))
                out.append(_entry("PASS", "command router",
                                  "onnx threshold %s, adapter routed by similarity"
                                  % config.get("threshold")))
        except (OSError, ValueError, KeyError) as exc:
            out.append(_entry("INFO", "command shadow", "onnx config unreadable: %s" % exc))
    elif not command_model.is_file():
        out.append(_entry("INFO", "command shadow",
                          "%s is absent; --command-shadow auto would log unavailable"
                          % command_model.name))
    else:
        missing = [name for name in ("torch", "tokenizers")
                   if importlib.util.find_spec(name) is None]
        if missing:
            out.append(_entry("INFO", "command shadow",
                              "artifact present; missing %s" % ", ".join(missing)))
        else:
            out.append(_entry("PASS", "command shadow",
                              "artifact and deps ready; opt in with --command-shadow auto"))
            router = command_model.with_name("router.json")
            if router.is_file():
                try:
                    config = json.loads(router.read_text(encoding="utf-8"))
                    refs = [ROOT / config[key] for key in ("base", "adapter", "exemplars")]
                    absent = [path.name for path in refs if not path.is_file()]
                    if absent:
                        out.append(_entry("WARN", "command router",
                                          "missing %s" % ", ".join(absent)))
                    else:
                        out.append(_entry("PASS", "command router",
                                          "threshold %s, adapter routed by similarity"
                                          % config.get("threshold")))
                except (OSError, ValueError, KeyError) as exc:
                    out.append(_entry("WARN", "command router",
                                      "unreadable: %s" % exc))
    return out


def exit_code(results: List[Dict[str, str]]) -> int:
    return 1 if any(item["level"] == "FAIL" for item in results) else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = checks(log=args.log)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        width = max(len(item["name"]) for item in results)
        for item in results:
            print("%-5s %-*s %s" % (item["level"], width, item["name"], item["detail"]))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
