"""Which egress actually works right now.

The app refuses to launch a client when no candidate is healthy (`fail_closed=1`), which is
correct — but it reports that as "auto mode found no healthy egress (tunnel blocked, pool
dead)", and from outside that reads like "the emulator is broken". It is not. The emulator is
fine; the exit is gone.

This asks each candidate the only question that matters — can you open a TCP tunnel to the
game's login host — and prints one line per candidate. Nothing here writes config.

    python ai/test_egress.py
    python ai/test_egress.py --host dt-proxy-production-login.ankama-games.com --port 443

Exit code 0 if at least one candidate works.
"""

from __future__ import annotations

import argparse
import base64
import re
import socket
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NETWORK_INI = REPO_ROOT.parent / "network.ini"
PROXIES = REPO_ROOT.parent / "proxies.txt"

DEFAULT_HOST = "dt-proxy-production-login.ankama-games.com"
DEFAULT_PORT = 443

# Loopback probes must not go through HTTP_PROXY.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_ini(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    except OSError:
        pass
    return out


def read_pool(path: Path) -> list[str]:
    entries = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entries.append(line)
    except OSError:
        pass
    return entries


def parse_proxy(entry: str):
    """'http://user:pass@host:port' -> (scheme, user, pass, host, port)."""
    m = re.match(r"(?:(http|https|socks5)://)?(?:([^:@/]+):([^@/]*)@)?([^:/]+):(\d+)", entry)
    if not m:
        return None
    scheme, user, pwd, host, port = m.groups()
    return (scheme or "http").lower(), user, pwd, host, int(port)


def mask(entry: str) -> str:
    return re.sub(r"//[^@]*@", "//***:***@", entry)


def _fetch_through(sock, target_host: str, timeout: float) -> tuple[bool, str]:
    """Actually fetch something through the tunnel.

    Answering CONNECT is not the same as carrying a request. Four proxies earlier today
    accepted CONNECT and returned 403 to every real fetch — `curl -x` gave 000 on both — while
    this script reported them as working. The app then launched and failed at the login screen.
    A check that answers a weaker question than it appears to is worse than no check.
    """
    import ssl as _ssl

    context = _ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = _ssl.CERT_NONE
    try:
        with context.wrap_socket(sock, server_hostname=target_host) as tls:
            tls.settimeout(timeout)
            tls.sendall(("GET /config.json HTTP/1.1\r\nHost: %s\r\n"
                         "User-Agent: Mozilla/5.0\r\nAccept: */*\r\n"
                         "Connection: close\r\n\r\n" % target_host).encode())
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                head += chunk
                if len(head) > 8192:
                    break
    except Exception as exc:  # noqa: BLE001
        return False, "tunnel did not carry a request: %s" % type(exc).__name__

    status = head.split(b"\r\n", 1)[0].split(b" ")[1:2]
    if status == [b"200"]:
        return True, "HTTP 200 through the tunnel"
    return False, "tunnel open but /config.json -> %s" % (status[0].decode() if status else "no reply")


def test_http(host: str, port: int, user, pwd, target_host: str, target_port: int,
              timeout: float) -> tuple[bool, str]:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return False, "connect failed: %s" % exc
    try:
        req = "CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n" % (
            target_host, target_port, target_host, target_port)
        if user:
            token = base64.b64encode(("%s:%s" % (user, pwd or "")).encode()).decode()
            req += "Proxy-Authorization: Basic %s\r\n" % token
        req += "User-Agent: Mozilla/5.0\r\n\r\n"
        s.sendall(req.encode())
        head = s.recv(200).decode("latin-1", "replace")
        status = head.splitlines()[0] if head else "(no response)"
        if " 200" in status:
            # The proxy agreed to relay. Now make it relay.
            return _fetch_through(s, target_host, timeout)
        if "403" in status:
            return False, status + " — rejected. Usually an IP allow-list, so a new public IP breaks it."
        if "407" in status:
            return False, status + " — needs credentials (user:pass@host:port in proxies.txt)."
        return False, status
    except OSError as exc:
        return False, "handshake failed: %s" % exc
    finally:
        try:
            s.close()
        except OSError:
            pass


def test_socks5(host: str, port: int, target_host: str, target_port: int,
                timeout: float) -> tuple[bool, str]:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return False, "connect failed: %s" % exc
    try:
        s.sendall(b"\x05\x01\x00")                      # version 5, one method, no auth
        hello = s.recv(2)
        if len(hello) < 2:
            return False, "no SOCKS5 greeting"
        if hello[0] != 5:
            return False, "not a SOCKS5 proxy"
        if hello[1] != 0:
            return False, "SOCKS5 requires authentication"
        name = target_host.encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name
                  + target_port.to_bytes(2, "big"))
        reply = s.recv(10)
        if len(reply) < 2:
            return False, "no SOCKS5 reply"
        if reply[1] == 0:
            return _fetch_through(s, target_host, timeout)
        codes = {1: "general failure", 2: "not allowed by ruleset", 3: "network unreachable",
                 4: "host unreachable", 5: "connection refused", 6: "TTL expired"}
        return False, "SOCKS5 refused: %s" % codes.get(reply[1], "code %d" % reply[1])
    except OSError as exc:
        return False, "handshake failed: %s" % exc
    finally:
        try:
            s.close()
        except OSError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST, help="target host to tunnel to")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--proxy", default="",
                    help="test one entry and nothing else, e.g. http://user:pass@host:port")
    ap.add_argument("--quiet", action="store_true",
                    help="one line of output; for scripts. Exit code says it.")
    args = ap.parse_args(argv)

    # One entry, nothing else. This is what `manage-proxy.bat add` calls before it writes a
    # line into proxies.txt: a proxy that is not tested before it is saved is a proxy the
    # next launch will fail closed on, with no record of why.
    if args.proxy:
        parsed = parse_proxy(args.proxy)
        if not parsed:
            print("cannot parse %r — expected [http|socks5]://[user:pass@]host:port"
                  % args.proxy)
            return 2
        scheme, user, pwd, host, port = parsed
        t0 = time.time()
        if scheme == "socks5":
            ok, why = test_socks5(host, port, args.host, args.port, args.timeout)
        else:
            ok, why = test_http(host, port, user, pwd, args.host, args.port, args.timeout)
        ms = (time.time() - t0) * 1000
        if args.quiet:
            print(("PASS %s" if ok else "FAIL %s") % mask(args.proxy))
        else:
            print("%-40s %-4s %6.0f ms  %s" % (mask(args.proxy), "PASS" if ok else "FAIL", ms, why))
        return 0 if ok else 1

    cfg = read_ini(NETWORK_INI)
    pool = read_pool(PROXIES)
    mode = cfg.get("mode", "auto")
    socks_port = cfg.get("socks_port", "")
    fail_closed = cfg.get("fail_closed", "1")

    print("target: %s:%d" % (args.host, args.port))
    print("config: mode=%s socks_port=%s fail_closed=%s  (%s)"
          % (mode, socks_port or "-", fail_closed, NETWORK_INI.name))
    print("pool:   %d entr%s in %s\n" % (len(pool), "y" if len(pool) == 1 else "ies",
                                         PROXIES.name))

    results = []

    # 1. The configured local tunnel (wireproxy binds a SOCKS5 port and nothing else).
    if socks_port:
        port = int(socks_port)
        t0 = time.time()
        ok, why = test_socks5("127.0.0.1", port, args.host, args.port, args.timeout)
        results.append(("socks5://127.0.0.1:%d" % port, ok, why, (time.time() - t0) * 1000))

    # 2. The pool, exactly as the app reads it.
    for entry in pool:
        parsed = parse_proxy(entry)
        if not parsed:
            results.append((mask(entry), False, "unparsable entry", 0.0))
            continue
        scheme, user, pwd, host, port = parsed
        t0 = time.time()
        if scheme == "socks5":
            ok, why = test_socks5(host, port, args.host, args.port, args.timeout)
        else:
            ok, why = test_http(host, port, user, pwd, args.host, args.port, args.timeout)
        results.append((mask(entry), ok, why, (time.time() - t0) * 1000))

    if not results:
        print("no candidates configured: proxies.txt is empty and socks_port is unset.")

    width = max((len(r[0]) for r in results), default=10)
    for label, ok, why, ms in results:
        print("  %-*s  %-4s  %6.0f ms  %s" % (width, label, "PASS" if ok else "FAIL", ms, why))

    working = [r for r in results if r[1]]
    print()
    if working:
        print("%d of %d candidates work. Fastest: %s"
              % (len(working), len(results), min(working, key=lambda r: r[3])[0]))
        return 0

    print("No candidate works. With fail_closed=%s the app will refuse to launch a client,"
          % fail_closed)
    print("which is the correct behaviour — a direct connection would expose this machine's")
    print("own address to the game.")
    print()
    print("Fix one of these, then re-run this script:")
    print("  - the pool proxy returns 403: its IP allow-list probably still has your old")
    print("    address. Ask the provider to allow your new one, or add a working entry as")
    print("    http://user:pass@host:port in proxies.txt")
    print("  - the tunnel is not listening: start wireproxy (it binds socks_port and")
    print("    nothing else, so Windows routing and DNS are untouched)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
