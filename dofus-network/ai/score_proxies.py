"""Rank the proxy pool by how stable it is likely to be, using the network-health model.

The pool's deep check answers one question: does this proxy open a tunnel to the login host
right now. That is necessary and not sufficient — a proxy that answers once and then stalls
is worse than one that is slower but steady, because the game notices the stall, not the
latency. The model in `networkhealth_artifacts/` forecasts RTT and estimates degradation from
a short history, which is exactly the missing judgement.

So: ping each candidate, feed the series to the model, and rank by what it says.

    python ai/score_proxies.py                  # every entry in proxies.txt
    python ai/score_proxies.py --samples 40     # longer history, better forecast
    python ai/score_proxies.py --top 5          # only show the best five

Honest about two things:

  * It pings the proxy itself, not the game path. That measures the hop you control.
  * The checkpoint was trained on public RIPE Atlas series. The REPORT says so and warns that
    public probe data cannot characterise this client; local telemetry is the better source.
    Treat the ranking as a tie-breaker between candidates that already pass the deep check,
    not as a promise about how the game will feel.
"""

from __future__ import annotations

import argparse
import socket
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = REPO_ROOT.parent
POOL = ROOT / "proxies.txt"
CHECKPOINT = REPO_ROOT / "networkhealth_artifacts" / "model.pt"

sys.path.insert(0, str(REPO_ROOT))

import re  # noqa: E402


def parse(entry: str):
    m = re.match(r"(?:(http|https|socks5)://)?(?:([^:@/]+):([^@/]*)@)?([^:/]+):(\d+)", entry)
    if not m:
        return None
    scheme, _user, _pwd, host, port = m.groups()
    return (scheme or "http").lower(), host, int(port)


def load_pool() -> list[str]:
    try:
        lines = POOL.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [l.strip() for l in lines
            if l.strip() and not l.startswith("#")
            and l.startswith(("http://", "https://", "socks5://"))]


def tcp_rtt(host: str, port: int, timeout: float) -> float | None:
    """One measurement: how long the TCP handshake takes, or None if it never completes."""
    started = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return (time.perf_counter() - started) * 1000.0
    except OSError:
        return None


def score(engine_cls, observation_cls, entry: str, samples: int, interval: float,
          timeout: float) -> dict:
    parsed = parse(entry)
    if not parsed:
        return {"entry": entry, "error": "unparsable"}
    _scheme, host, port = parsed

    engine = engine_cls(CHECKPOINT)
    rtts: list[float] = []
    losses = 0
    result = None
    for i in range(samples):
        rtt = tcp_rtt(host, port, timeout)
        if rtt is None:
            losses += 1
        else:
            rtts.append(rtt)
        out = engine.observe(observation_cls(timestamp=time.time(), rtt_ms=rtt))
        if out is not None:
            result = out
        if i + 1 < samples:
            time.sleep(interval)

    data = result.as_dict() if result is not None else {}
    degradation = max(data.get("degradation_probability") or [0.0]) if data else None
    loss_prob = max(data.get("loss_probability") or [0.0]) if data else None
    forecast = [r for r in (data.get("rtt_ms") or []) if r is not None]
    return {
        "entry": entry,
        "samples": len(rtts) + losses,
        "answered": len(rtts),
        "losses": losses,
        "rtt_median": statistics.median(rtts) if rtts else None,
        "rtt_p95": (sorted(rtts)[int(round(0.95 * (len(rtts) - 1)))] if rtts else None),
        "jitter": (statistics.pstdev(rtts) if len(rtts) > 1 else 0.0),
        "forecast": forecast[-1] if forecast else None,
        "degradation": degradation,
        "loss_prob": loss_prob,
        "confidence": data.get("confidence"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", type=int, default=36,
                    help="pings per proxy (the model needs a full window, 32 by default)")
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between pings")
    ap.add_argument("--timeout", type=float, default=4.0, help="per-ping timeout")
    ap.add_argument("--top", type=int, default=0, help="show only the best N")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    pool = load_pool()
    if not pool:
        print("proxies.txt has no entries. Run: manage-proxy.bat refresh")
        return 1
    if not CHECKPOINT.exists():
        print("no model at %s" % CHECKPOINT)
        return 2

    try:
        from networkhealth.inference import NetworkHealthInference, Observation
    except Exception as exc:  # noqa: BLE001
        print("cannot import the inference runtime: %s" % exc)
        return 2

    # Fail early and clearly if the checkpoint does not match the code, rather than once per
    # proxy after a minute of pinging.
    try:
        NetworkHealthInference(CHECKPOINT)
    except Exception as exc:  # noqa: BLE001
        print("the checkpoint does not load against this code: %s" % str(exc).split("\n")[0])
        print("retrain it:  python -m networkhealth prepare --seq-len 32  then  train")
        return 2

    print("scoring %d candidate(s), %d pings each, %.1fs apart\n"
          % (len(pool), args.samples, args.interval))
    rows = []
    for entry in pool:
        row = score(NetworkHealthInference, Observation, entry, args.samples,
                    args.interval, args.timeout)
        rows.append(row)
        if not args.json:
            if row.get("error"):
                print("  %-40s %s" % (entry, row["error"]))
            else:
                print("  %-40s median %5s ms  p95 %5s ms  loss %d/%d  degradation %s"
                      % (entry,
                         "%.0f" % row["rtt_median"] if row["rtt_median"] else "-",
                         "%.0f" % row["rtt_p95"] if row["rtt_p95"] else "-",
                         row["losses"], row["samples"],
                         "%.0f%%" % (100 * row["degradation"]) if row["degradation"] is not None
                         else "-"))

    scored = [r for r in rows if not r.get("error")]

    # Rank on what was measured, not on what was predicted.
    #
    # The model's window was built from pings a few hundred milliseconds apart, and its
    # training data is RIPE Atlas at roughly a minute apart — `log_gap_seconds` is one of its
    # ten features, so the cadence is not a detail, it is an input. On a fast series the model
    # is out of distribution and its forecast is decoration. Measured loss, jitter and p95 are
    # true at any cadence, so they decide the order and the model is reported beside them.
    def rank(row):
        loss_rate = row["losses"] / max(1, row["samples"])
        jitter_ratio = (row["jitter"] or 0.0) / max(1.0, row["rtt_median"] or 1.0)
        return (loss_rate, round(jitter_ratio, 3), row["rtt_p95"] or 1e9)

    scored.sort(key=rank)
    if args.top:
        scored = scored[:args.top]

    if args.json:
        import json
        print(json.dumps(scored, indent=1))
        return 0

    print("\n== ranking (measured: loss, then jitter, then p95) ==")
    for i, row in enumerate(scored, 1):
        print("  %d. %-36s loss %d/%d  jitter %s ms  p95 %s ms   [model: %.0f%% degrad]"
              % (i, row["entry"], row["losses"], row["samples"],
                 "%.0f" % (row["jitter"] or 0.0),
                 "%.0f" % row["rtt_p95"] if row["rtt_p95"] else "-",
                 100 * (row["degradation"] or 0.0)))

    if not scored:
        print("  no proxy produced a full window of measurements, so there is nothing to rank")
        return 1
    print("\nThe model's window was trained on ~60s-spaced probe data and these pings are")
    print("%.1fs apart, so its degradation column is advisory at best. The loss and jitter" % args.interval)
    print("columns are measured and hold at any cadence — they decide the order.")
    print("\nbest right now: %s" % scored[0]["entry"])
    print("(it pings the proxy, not the game path — use it to choose between candidates that")
    print(" already pass the deep check, not as a prediction of how play will feel)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
