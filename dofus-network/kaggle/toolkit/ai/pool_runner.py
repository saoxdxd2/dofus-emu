"""Keep proxies.txt alive using nothing but the interpreter the installer already ships.

`scripts/proxy_pool.js` is the reference implementation and it works. It also needs Node, and
the payload does not contain Node — it contains Python. So on an installed copy the pool could
be read and probed but never refreshed, which is exactly how it decayed to a single dead entry
while every launch refused with "no healthy egress". A tool that cannot run where it is needed
is not a tool.

This is the same gate, in Python, against the same contract:

  1. fetch candidate lists over HTTPS
  2. screen TCP at a short timeout (most candidates are dead before TLS)
  3. deep check: proxy CONNECT -> TLS to the login host -> GET each path -> require 200
  4. rewrite proxies.txt atomically, header first, one entry per line

The gate is not negotiable: both paths must answer 200. A proxy that opens a tunnel but cannot
serve `/config.json` will fail at the point the game needs it, which is worse than failing here.

    python ai/pool_runner.py                    # one pass over 400 candidates
    python ai/pool_runner.py --batch 1200       # deeper hunt
    python ai/pool_runner.py --check-only       # re-verify what is in proxies.txt, no fetching

Exit code is 0 when at least one entry survives, 1 when none do — the same convention as
ai/test_egress.py, so a watchdog can act on it without parsing text.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = REPO_ROOT.parent
POOL = ROOT / "proxies.txt"
RAW = ROOT / "proxies_raw.txt"

TARGET_HOST = "dt-proxy-production-login.ankama-games.com"
TARGET_PORT = 443
TARGET_PATHS = ("/build/primus.js", "/config.json")
UA = ("Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36")

SOURCES = (
    ("http", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"),
    ("http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"),
    ("socks5", "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt"),
    ("http", "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/zloi-user/hideip.me/main/socks5.txt"),
    ("http", "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt"),
    ("http", "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt"),
    ("http", "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt"),
    ("socks5", "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt"),
    ("http", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"),
    ("socks5", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all&ssl=all&anonymity=all"),
    ("http", "https://www.proxy-list.download/api/v1/get?type=http"),
    ("socks5", "https://www.proxy-list.download/api/v1/get?type=socks5"),
)

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(message: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def fetch(url: str, timeout: float = 15.0) -> str | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with _OPENER.open(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            return response.read(8_000_000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - any failure means "this source is down"
        return None


def parse_line(line: str, default_scheme: str) -> str | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if "://" in text:
        scheme, _, rest = text.partition("://")
        scheme = scheme.lower()
        if scheme == "https":
            scheme = "http"
        if scheme not in ("http", "socks5"):
            return None
    else:
        scheme, rest = default_scheme, text
    rest = rest.split("#", 1)[0].strip().rstrip("/")
    if "," in rest:                       # "1.2.3.4,80,US,elite" from the CSV APIs
        rest = rest.replace(",", ":", 1).split(",", 1)[0]
    host, _, port = rest.rpartition(":")
    if not host or not port.isdigit():
        return None
    if not (1 <= int(port) <= 65535):
        return None
    return "%s://%s:%s" % (scheme, host, int(port))


def split_entry(entry: str):
    scheme, _, rest = entry.partition("://")
    userinfo = ""
    if "@" in rest:
        userinfo, _, rest = rest.rpartition("@")
    host, _, port = rest.rpartition(":")
    return scheme, host, int(port), userinfo


def tcp_ok(entry: str, timeout: float) -> bool:
    _scheme, host, port, _userinfo = split_entry(entry)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_connect(sock, host: str, port: int, userinfo: str) -> bool:
    headers = "CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n" % (host, port, host, port)
    if userinfo:
        import base64
        token = base64.b64encode(userinfo.encode()).decode()
        headers += "Proxy-Authorization: Basic %s\r\n" % token
    sock.sendall((headers + "\r\n").encode())
    reply = b""
    while b"\r\n\r\n" not in reply:
        chunk = sock.recv(4096)
        if not chunk:
            return False
        reply += chunk
        if len(reply) > 8192:
            return False
    return reply.split(b"\r\n", 1)[0].split(b" ")[1:2] == [b"200"]


def _socks5_connect(sock, host: str, port: int, userinfo: str) -> bool:
    sock.sendall(b"\x05\x01\x00" if not userinfo else b"\x05\x02\x00\x02")
    hello = sock.recv(2)
    if len(hello) < 2 or hello[0] != 5:
        return False
    if hello[1] == 2:
        user, _, password = userinfo.partition(":")
        sock.sendall(b"\x01" + bytes([len(user)]) + user.encode()
                     + bytes([len(password)]) + password.encode())
        if sock.recv(2)[1] != 0:
            return False
    name = host.encode()
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + port.to_bytes(2, "big"))
    reply = sock.recv(10)
    return len(reply) > 1 and reply[1] == 0


def deep_check(entry: str, timeout: float) -> tuple[bool, str]:
    """The gate: both paths must answer 200 through the proxy."""
    scheme, host, port, userinfo = split_entry(entry)
    for path in TARGET_PATHS:
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            return False, "connect: %s" % type(exc).__name__
        try:
            sock.settimeout(timeout)
            if scheme == "socks5":
                if not _socks5_connect(sock, TARGET_HOST, TARGET_PORT, userinfo):
                    return False, "socks5 refused"
            else:
                if not _http_connect(sock, TARGET_HOST, TARGET_PORT, userinfo):
                    return False, "proxy refused CONNECT"

            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with context.wrap_socket(sock, server_hostname=TARGET_HOST) as tls:
                tls.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n"
                             "Accept: */*\r\nConnection: close\r\n\r\n"
                             % (path, TARGET_HOST, UA)).encode())
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    head += chunk
                    if len(head) > 8192:
                        break
            status = head.split(b"\r\n", 1)[0].split(b" ")[1:2]
            if status != [b"200"]:
                return False, "http %s on %s" % (status[0].decode() if status else "?", path)
        except Exception as exc:  # noqa: BLE001
            return False, "%s on %s" % (type(exc).__name__, path)
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return True, "ok"


def read_pool() -> list[str]:
    try:
        return [line.strip() for line in POOL.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip() and not line.startswith("#")
                and line.strip().startswith(("http://", "socks5://"))]
    except OSError:
        return []


def write_pool(entries: list[str]) -> None:
    header = ("# proxies.txt - managed by ai/pool_runner.py (do not edit while it runs)\n"
              "# updated: %s | live entries: %d\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), len(entries)))
    tmp = POOL.with_suffix(".txt.tmp")
    tmp.write_text(header + "".join(e + "\n" for e in entries), encoding="utf-8")
    os.replace(tmp, POOL)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", type=int, default=400, help="candidates to deep-check")
    ap.add_argument("--timeout", type=float, default=8.0, help="per-stage timeout")
    ap.add_argument("--connect-timeout", type=float, default=1.5, help="TCP screen timeout")
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--check-only", action="store_true",
                    help="re-verify proxies.txt and rewrite it; do not fetch anything new")
    args = ap.parse_args(argv)

    started = time.time()
    keep: list[str] = []
    seen: set[str] = set()

    existing = read_pool()
    if existing:
        log("re-verifying %d entry(ies) already in proxies.txt" % len(existing))
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(lambda e: (e, deep_check(e, args.timeout)), existing))
        for entry, (ok, why) in results:
            if ok:
                keep.append(entry)
                seen.add(entry)
            else:
                log("  drop %-34s %s" % (entry, why))
        log("  %d of %d survived" % (len(keep), len(existing)))

    if not args.check_only:
        candidates: list[str] = []
        for scheme, url in SOURCES:
            body = fetch(url)
            if body is None:
                continue
            for line in body.splitlines():
                entry = parse_line(line, scheme)
                if entry and entry not in seen:
                    seen.add(entry)
                    candidates.append(entry)
        log("fetched %d new candidate(s) from %d source(s)" % (len(candidates), len(SOURCES)))

        # Newest first: candidates from the tail of a fresh fetch are the ones the lists just
        # published, and public proxies die within hours.
        batch = candidates[-args.batch:][::-1]
        if batch:
            log("screening TCP on %d candidate(s)" % len(batch))
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                alive = [e for e, ok in zip(batch, pool.map(
                    lambda e: tcp_ok(e, args.connect_timeout), batch)) if ok]
            log("  %d answered a connect; deep-checking those" % len(alive))

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                results = list(pool.map(lambda e: (e, deep_check(e, args.timeout)), alive))
            for entry, (ok, why) in results:
                if ok:
                    keep.append(entry)
                    log("  OK: %s" % entry)
                else:
                    log("  no: %-34s %s" % (entry, why))

    # Deduplicate, preserving order.
    final: list[str] = []
    for entry in keep:
        if entry not in final:
            final.append(entry)

    write_pool(final)
    log("cycle done in %.1fs | live=%d" % (time.time() - started, len(final)))
    if not final:
        log("no working exit. fail_closed means the app will refuse to launch a client,")
        log("which is correct: a direct connection would expose this machine's own address.")
    return 0 if final else 1


if __name__ == "__main__":
    raise SystemExit(main())
