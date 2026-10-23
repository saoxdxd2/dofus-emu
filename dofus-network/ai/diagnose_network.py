"""Ask the network-health model whether the relay path is degrading.

The project trained a small RTT forecaster with a loss and a degradation head
(`networkhealth_artifacts/model.pt`, 52k parameters, 2 ms per prediction). Its
`REPORT.md` is explicit that public RIPE Atlas data cannot characterise *this* client and
that local authorised telemetry is what it needs. The relay already publishes exactly
that on `/__telemetry` — RTT, bytes, connection counts and connection failures per port.

So this reads the relays this machine is actually running, feeds the samples through the
model, and prints one verdict. It is the difference between "I think it's the proxy" and
a number.

    python ai/diagnose_network.py                 # 128 samples, 0.5 s apart (~64 s)
    python ai/diagnose_network.py --seconds 20    # quicker, less confident
    python ai/diagnose_network.py --ports 8884    # one relay

Loopback is contacted with a direct opener: HTTP_PROXY is set machine-wide on a box
running a VPN or proxy client, and routing a 127.0.0.1 request through it is how you get
a 502 from your own machine.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ARTIFACTS = REPO_ROOT / "networkhealth_artifacts"
# Which checkpoint: see networkhealth.inference.resolve_checkpoint. This tool samples at 0.5 s,
# which is the local cadence, so it should not be quoting the RIPE model's forecast.
from networkhealth.inference import resolve_checkpoint  # noqa: E402

CHECKPOINT, _CK_KIND, _CK_NOTE = resolve_checkpoint(ARTIFACTS)

# Loopback must never go through HTTP_PROXY.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_telemetry(port: int, timeout: float = 1.5) -> dict | None:
    """One sample from a relay, or None if it is not there."""
    try:
        with _OPENER.open("http://127.0.0.1:%d/__telemetry" % port, timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def find_relays(ports: list[int]) -> dict[int, dict]:
    """Every relay on this machine that answers, with one sample each."""
    live: dict[int, dict] = {}
    for port in ports:
        sample = read_telemetry(port, timeout=0.8)
        if sample:
            live[port] = sample
    return live


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ports", default="",
                    help="comma-separated relay ports; default: probe 8880-8899")
    ap.add_argument("--samples", type=int, default=128,
                    help="observations to feed the model (default 128, its window size)")
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between samples")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="collect for this long instead of --samples")
    ap.add_argument("--json", action="store_true", help="print raw output")
    args = ap.parse_args(argv)

    if not CHECKPOINT.exists():
        print("no model at %s" % CHECKPOINT)
        print("build one: ai/collect_local.py, then networkhealth prepare/train")
        return 2
    if _CK_KIND != "local":
        print("model: the global checkpoint (%s)" % _CK_NOTE)

    ports = ([int(p) for p in args.ports.split(",") if p.strip()]
             if args.ports else list(range(8880, 8900)))
    live = find_relays(ports)
    if not live:
        print("no relay is answering /__telemetry on %s" % ports[:6])
        print("  start an instance in DofusFarm first — the model needs local telemetry,")
        print("  and its own REPORT.md says public probe data cannot stand in for it.")
        return 1

    print("relays answering: %s" % ", ".join(str(p) for p in sorted(live)))
    port = max(live, key=lambda p: (live[p].get("conns_total") or 0))
    print("using port %d (most connections: %s)" % (port, live[port].get("conns_total")))
    print("collecting %s samples every %.2fs ..." %
          (args.samples if not args.seconds else "~%ds of" % args.seconds, args.interval))

    from networkhealth.inference import NetworkHealthInference, Observation

    # The model is optional on purpose.
    #
    # networkhealth_artifacts/model.pt was trained before the architecture changed
    # (BatchNorm -> GroupNorm) and before SEQ_LEN went 128 -> 32, so it no longer loads
    # against this code. Refusing to run, or loading it with strict=False and printing
    # numbers anyway, would both be worse than saying so: the telemetry below is real,
    # and a forecast from half-loaded weights is a guess wearing a decimal point.
    engine = None
    model_error = None
    try:
        engine = NetworkHealthInference(CHECKPOINT)
    except Exception as exc:  # noqa: BLE001
        model_error = "%s: %s" % (type(exc).__name__, str(exc).split("\n")[0][:160])
    rtts: list[float] = []
    losses = 0
    failed_start = live[port].get("conns_failed") or 0
    deadline = time.time() + args.seconds if args.seconds > 0 else None
    taken = 0
    last = None

    while True:
        if deadline is not None and time.time() >= deadline:
            break
        if deadline is None and taken >= args.samples:
            break
        sample = read_telemetry(port)
        taken += 1
        now = time.time()
        if sample is None:
            losses += 1
            if engine is not None:
                engine.observe(Observation(timestamp=now, rtt_ms=None, sent=0, received=0))
        else:
            last = sample
            rtt = sample.get("rtt_ms")
            if isinstance(rtt, (int, float)):
                rtts.append(float(rtt))
            if engine is not None:
                engine.observe(Observation(
                    timestamp=now,
                rtt_ms=float(rtt) if isinstance(rtt, (int, float)) else None,
                    sent=int(sample.get("sent_bytes") or 0),
                    received=int(sample.get("recv_bytes") or 0)))
        time.sleep(args.interval)

    print("\n== what the relay reported ==")
    print("  samples: %d, no reply: %d" % (taken, losses))
    if rtts:
        print("  rtt  min %.1f  avg %.1f  p95 %.1f  max %.1f  stdev %.1f ms"
              % (min(rtts), statistics.fmean(rtts), percentile(rtts, 0.95),
                 max(rtts), statistics.pstdev(rtts) if len(rtts) > 1 else 0.0))
    if last:
        failed = last.get("conns_failed") or 0
        print("  connections: %s total, %s failed (%+d during this run), %s active"
              % (last.get("conns_total"), failed, failed - failed_start,
                 last.get("conns_active")))
        print("  proxy's own average rtt: %s ms" % last.get("rtt_avg_ms"))

    if engine is None:
        print("\n== model ==")
        print("  not loaded: %s" % model_error)
        print("  The checkpoint predates this code (BatchNorm -> GroupNorm, SEQ_LEN")
        print("  128 -> 32). Retrain with `python -m networkhealth train` and the")
        print("  forecast below becomes available; the telemetry above is unaffected.")
        data = None
    else:
        # One more observation returns the forecast once the window is full.
        result = engine.observe(Observation(timestamp=time.time(),
                                            rtt_ms=rtts[-1] if rtts else None))
        if result is None:
            print("\n== model ==")
            print("  window not full yet after %d observations; collect longer"
                  % taken)
            data = None
        else:
            data = result.as_dict()

    if data is None:
        worst_loss, worst_deg = 0.0, 0.0
    else:
        if args.json:
            print(json.dumps(data, indent=1))
            return 0

        print("\n== model ==")
        for i, h in enumerate(data["horizons_observations"]):
            rtt = data["rtt_ms"][i]
            if rtt is None:
                continue
            print("  +%-3d obs: rtt %.1f ms  loss %.0f%%  degradation %.0f%%"
                  % (h, rtt, 100 * data["loss_probability"][i],
                     100 * data["degradation_probability"][i]))
        print("  suspicion %.2f, confidence %s, window fill %.0f%%"
              % (data["suspicion_score"], data["confidence"], 100 * data["window_fill"]))
        for caveat in data.get("caveats") or []:
            print("  note: %s" % caveat)
        worst_loss = max(data["loss_probability"] or [0.0])
        worst_deg = max(data["degradation_probability"] or [0.0])

    spread = (percentile(rtts, 0.95) - min(rtts)) if rtts else 0.0
    print("\n== verdict ==")
    if losses or worst_loss >= 0.2:
        print("  The relay is dropping samples (%.0f%% of them). That is a transport"
              % (100 * (losses / taken if taken else 0)))
        print("  problem: the game connection can die mid-session.")
    elif worst_deg >= 0.5:
        print("  RTT is degrading (%.0f%% probability at the furthest horizon). Sessions"
              % (100 * worst_deg))
        print("  will feel laggy and can time out on long map changes.")
    elif spread > 80:
        print("  RTT is stable on average but spiky (%.0f ms between min and p95). Spikes"
              % spread)
        print("  are what drop a long request, not the average.")
    else:
        print("  The relay path looks healthy: no lost samples, %.0f ms spread, loss"
              % spread)
        print("  probability %.0f%%. If a session still ends, it is not the transport."
              % (100 * worst_loss))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
