"""Collect local RTT telemetry at the cadence the model will actually be used at.

The shipped checkpoint was trained on RIPE Atlas probes at roughly a minute apart. That is a
fine corpus for a public-probe forecaster and the wrong one for this project: `log_gap_seconds`
is one of the model's ten features, so when `ai/score_proxies.py` pings a proxy twice a second
the model is out of distribution and its forecast is decoration. Measured, not assumed — a
steady 120 ms series came back as "0.8 ms, confidence high".

The fix is the Phase 5 the old REPORT asked for: a corpus from *this* machine, at *this*
cadence. This writes the same JSONL shape `networkhealth prepare` already consumes, so nothing
downstream changes.

    python ai/collect_local.py --minutes 15              # ~900 samples per target
    python ai/collect_local.py --minutes 60 --interval 2
    python ai/collect_local.py --relay                   # also sample the running relay

Output: networkhealth_artifacts/local.jsonl

A failed probe is written as `rtt_ms: null`, not skipped. Loss is one of the model's targets,
and a corpus that only contains successes teaches it that nothing ever drops.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "networkhealth_artifacts" / "local.jsonl"

DEFAULT_TARGETS = ["8.8.8.8:443", "104.16.0.1:443", "dofus-touch.com:443"]

# Loopback must not go through HTTP_PROXY.
_OPENER = None


def _opener():
    global _OPENER
    if _OPENER is None:
        import urllib.request
        _OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return _OPENER


def series_id(target: str) -> int:
    """Stable, positive id per target, so repeated runs stay in the same series."""
    return zlib.crc32(target.encode("utf-8")) % (2 ** 31)


def tcp_rtt(host: str, port: int, timeout: float) -> float | None:
    started = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return (time.perf_counter() - started) * 1000.0
    except OSError:
        return None


def relay_ports(base: int, span: int = 6) -> list[int]:
    return list(range(base, base + span))


def relay_rtt(port: int, timeout: float) -> float | None:
    """RTT as the relay itself measured it, or None if it is not running."""
    try:
        with _opener().open("http://127.0.0.1:%d/__telemetry" % port, timeout=timeout) as r:
            if r.status != 200:
                return None
            data = json.loads(r.read().decode("utf-8", "replace"))
        value = data.get("rtt_ms")
        return float(value) if isinstance(value, (int, float)) else None
    except Exception:  # noqa: BLE001 - any failure is "no reading"
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=15.0, help="how long to collect")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between samples")
    ap.add_argument("--timeout", type=float, default=3.0, help="per-probe timeout")
    ap.add_argument("--targets", default=",".join(DEFAULT_TARGETS),
                    help="host:port list, comma separated")
    ap.add_argument("--relay", action="store_true",
                    help="also sample the running relay's own RTT on 8880..8885")
    ap.add_argument("--base-port", type=int, default=8880)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    targets = []
    for entry in args.targets.split(","):
        entry = entry.strip()
        if not entry:
            continue
        host, _, port = entry.rpartition(":")
        if not host:
            host, port = entry, "443"
        try:
            addr = socket.gethostbyname(host)
        except OSError as exc:
            print("skipping %s: %s" % (entry, exc))
            continue
        targets.append((entry, addr, int(port)))

    relay = relay_ports(args.base_port) if args.relay else []
    if not targets and not relay:
        print("nothing to sample")
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.minutes * 60
    written = 0
    failures = 0
    samples = 0

    print("collecting for %.0f min at %.1fs: %d target(s)%s"
          % (args.minutes, args.interval, len(targets),
             " + %d relay port(s)" % len(relay) if relay else ""))

    with out.open("w", encoding="utf-8") as handle:
        try:
            while time.time() < deadline:
                started = time.time()
                for label, addr, port in targets:
                    rtt = tcp_rtt(addr, port, args.timeout)
                    samples += 1
                    if rtt is None:
                        failures += 1
                    handle.write(json.dumps({
                        "source": "local",
                        "measurement_id": series_id(label),
                        "probe_id": 0,
                        "timestamp": round(started, 3),
                        "rtt_ms": None if rtt is None else round(rtt, 2),
                        "target": label,
                    }) + "\n")
                    written += 1
                for port in relay:
                    rtt = relay_rtt(port, args.timeout)
                    samples += 1
                    if rtt is None:
                        failures += 1
                    handle.write(json.dumps({
                        "source": "relay",
                        "measurement_id": port,
                        "probe_id": 0,
                        "timestamp": round(started, 3),
                        "rtt_ms": None if rtt is None else round(rtt, 2),
                        "target": "127.0.0.1:%d" % port,
                    }) + "\n")
                    written += 1
                handle.flush()

                if written % 200 < len(targets) + len(relay):
                    elapsed = (args.minutes * 60) - (deadline - time.time())
                    print("  %5.0fs  %6d rows  %d failed" % (elapsed, written, failures))

                sleep_for = args.interval - (time.time() - started)
                if sleep_for > 0:
                    time.sleep(sleep_for)
        except KeyboardInterrupt:
            print("\nstopped early; keeping what was collected")

    print("\nwrote %d rows to %s (%d failed, %.1f%%)"
          % (written, out, failures, 100.0 * failures / max(1, samples)))
    if written < 200:
        print("note: fewer than 200 rows is not enough to build windows; collect longer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
