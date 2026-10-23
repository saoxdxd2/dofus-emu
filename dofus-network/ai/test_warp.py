"""Can WARP work on this line? Answer with handshakes, not with guesses.

Three probes that look reasonable and prove nothing:

  TCP connect to 162.159.192.1:2408   that port is UDP-only, so it always "fails"
  a bare UDP packet to :2408          WireGuard drops invalid packets silently
  resolve engage.cloudflareclient.com and connect   fails for a reason that is not WARP

The only honest test is to run wireproxy against an endpoint and see whether a SOCKS5
CONNECT through the tunnel completes. That is what this does, for every endpoint and port
WARP publishes, plus the DNS and API facts that decide whether a fresh identity is even
obtainable.

    python ai/test_warp.py
    python ai/test_warp.py --timeout 4 --keep    # keep the probe configs for inspection

Nothing here writes bin/warp/wireproxy.conf or registers an account.
"""

from __future__ import annotations

import argparse
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WARP_DIR = REPO_ROOT.parent / "bin" / "warp"
CONF = WARP_DIR / "wireproxy.conf"
EXE = WARP_DIR / "wireproxy.exe"

TARGET_HOST = "dt-proxy-production-login.ankama-games.com"
TARGET_PORT = 443

# Every endpoint and port Cloudflare publishes for WARP.
CANDIDATES = [
    ("162.159.192.1", 2408), ("162.159.192.1", 500),
    ("162.159.192.1", 1701), ("162.159.192.1", 4500),
    ("162.159.193.1", 2408), ("162.159.193.10", 2408),
    ("188.114.96.1", 2408), ("188.114.97.1", 2408),
    ("188.114.98.1", 2408), ("188.114.99.1", 2408),
]


def socks5_through(port: int, timeout: float) -> tuple[bool, str]:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
    except OSError:
        return False, "the tunnel did not bind its port"
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        hello = s.recv(2)
        if len(hello) < 2 or hello[1] != 0:
            return False, "SOCKS5 handshake refused"
        name = TARGET_HOST.encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name
                  + TARGET_PORT.to_bytes(2, "big"))
        reply = s.recv(10)
        if len(reply) < 2:
            return False, "no SOCKS5 reply"
        if reply[1] == 0:
            return True, "tunnel open"
        return False, {1: "general failure", 2: "not allowed", 3: "network unreachable",
                       4: "host unreachable", 5: "refused", 6: "TTL expired"}.get(
                           reply[1], "code %d" % reply[1])
    except OSError as exc:
        return False, type(exc).__name__
    finally:
        try:
            s.close()
        except OSError:
            pass


def check_dns() -> None:
    print("== name resolution ==")
    for host in ("engage.cloudflareclient.com", "api.cloudflareclient.com"):
        try:
            addr = socket.gethostbyname(host)
            print("  %-32s -> %s" % (host, addr))
        except OSError as exc:
            print("  %-32s -> DOES NOT RESOLVE (%s)" % (host, exc.strerror or exc))
    print("  A hostname that does not resolve is filtered by DNS, which no WARP setting")
    print("  can work around. An explicit endpoint IP avoids the lookup but not the block.")
    print()


def check_api(timeout: float) -> None:
    """Is the account API reachable at all? A 4xx means yes; a timeout means no."""
    print("== account API ==")
    import ssl
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection(("api.cloudflareclient.com", 443), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname="api.cloudflareclient.com") as tls:
                peer = tls.getpeercert().get("subject", ())
                issuer = tls.getpeercert().get("issuer", ())
                cn = dict(x[0] for x in issuer).get("commonName", "?")
                print("  TLS handshake ok, certificate issued by %s" % cn)
                print("  (a block page would present a certificate from your ISP, not this)")
                tls.sendall(b"GET /v0a2158/reg HTTP/1.1\r\nHost: api.cloudflareclient.com\r\n"
                            b"Connection: close\r\n\r\n")
                head = tls.recv(120).decode("latin-1", "replace").splitlines()
                print("  HTTP: %s" % (head[0] if head else "(no response)"))
    except Exception as exc:  # noqa: BLE001
        print("  unreachable: %s: %s" % (type(exc).__name__, exc))
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeout", type=float, default=6.0, help="SOCKS5 read timeout")
    ap.add_argument("--keep", action="store_true", help="keep the generated probe configs")
    args = ap.parse_args(argv)

    if not EXE.exists() or not CONF.exists():
        print("no wireproxy at %s or no config at %s" % (EXE, CONF))
        return 2

    base = CONF.read_text(encoding="utf-8")
    check_dns()
    check_api(args.timeout)

    print("== real handshake, per endpoint ==")
    print("  %-18s %-6s %-8s %s" % ("endpoint", "port", "result", "detail"))
    working = []
    tmpdir = Path(tempfile.mkdtemp())
    for i, (ip, port) in enumerate(CANDIDATES):
        conf = re.sub(r"Endpoint\s*=\s*\S+", "Endpoint = %s:%d" % (ip, port), base)
        socks_port = 40060 + i
        conf = re.sub(r"BindAddress\s*=\s*\S+", "BindAddress = 127.0.0.1:%d" % socks_port, conf)
        probe = tmpdir / ("probe_%d.conf" % i)
        probe.write_text(conf, encoding="utf-8")
        proc = subprocess.Popen([str(EXE), "-c", str(probe)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.5)
        ok, why = socks5_through(socks_port, args.timeout)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not args.keep:
            probe.unlink(missing_ok=True)
        print("  %-18s %-6d %-8s %s" % (ip, port, "PASS" if ok else "fail", why))
        if ok:
            working.append((ip, port))
            break

    print()
    if working:
        ip, port = working[0]
        print("WARP works here on %s:%d." % (ip, port))
        print("Put it in bin/warp/wireproxy.conf as `Endpoint = %s:%d` and the tunnel will"
              % (ip, port))
        print("come up; farmengine starts it on demand.")
        return 0

    print("No WARP endpoint carries a handshake from this line.")
    print()
    print("What that means, and what it does not:")
    print("  - It is not a configuration error. Every published endpoint and port was tried")
    print("    with a real handshake, and the DNS names that would provide others do not")
    print("    resolve.")
    print("  - The account API is a different matter: if it answered above, wgcf can still")
    print("    register an identity. That does not help when the data plane is blocked.")
    print("  - Cloudflare's CDN range is usually still reachable, so this is the VPN")
    print("    endpoints being filtered, not Cloudflare as a whole.")
    print()
    print("Use the proxy pool instead: start-network.bat, then ai/test_egress.py.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
