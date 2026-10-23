"""Real-time AI Network Health Bridge.

Monitors running proxy instances (ports 8880..8883), feeds real-time telemetry
ticks into MultiInstanceNetworkHealth (TRM-MoE model), and exports continuous
multi-horizon degradation forecasts and failover alerts.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .inference import ForecastResult, MultiInstanceNetworkHealth, Observation

logger = logging.getLogger("networkhealth.bridge")


# Loopback telemetry must not go through HTTP_PROXY.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class NetworkHealthBridge:
    def __init__(
        self,
        checkpoint_path: Path | str,
        ports: list[int] | None = None,
        poll_interval: float = 1.5,
        output_path: Path | str = "networkhealth_artifacts/live_telemetry.json",
        degradation_threshold: float = 0.70,
    ) -> None:
        self.checkpoint_path = Path(checkpoint_path)
        self.ports = ports or [8880, 8881, 8882, 8883]
        self.poll_interval = poll_interval
        self.output_path = Path(output_path)
        self.degradation_threshold = degradation_threshold

        self.engine = MultiInstanceNetworkHealth(self.checkpoint_path)
        self._last_metrics: dict[int, dict[str, Any]] = {}
        self._pending_predictions: dict[str, tuple[float, float]] = {}  # slot_id -> (pred_rtt, pred_std)
        self._ewma_z: dict[str, float] = {}                                     # slot_id -> smoothed residual z-score
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def fetch_proxy_telemetry(self, port: int, timeout: float = 1.0) -> dict[str, Any] | None:
        url = f"http://127.0.0.1:{port}/__telemetry"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NetworkHealthBridge/1.0"})
            # The relay is on this machine. urlopen() honours HTTP_PROXY, and with a VPN
            # or proxy client running that is set machine-wide — so the health collector
            # was asking an external proxy to fetch a loopback port, which is both wrong
            # and a second way to get a 502 that has nothing to do with the network.
            with _LOCAL_OPENER.open(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = resp.read()
                    return json.loads(data.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            pass
        return None

    def poll_once(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        now = time.time()

        for port in self.ports:
            slot_id = f"slot-{port - 8880}"
            raw = self.fetch_proxy_telemetry(port)

            if raw is None:
                results[slot_id] = {
                    "port": port,
                    "online": False,
                    "status": "OFFLINE",
                    "rtt_ms": None,
                    "degradation_probability": None,
                    "loss_probability": None,
                    "residual_z_score": None,
                    "route_anomaly": False,
                }
                continue

            rtt_ms = float(raw.get("rtt_ms", 0.0))
            if rtt_ms <= 0.0:
                rtt_ms = float(raw.get("rtt_avg_ms", 35.0))
                if rtt_ms <= 0.0:
                    rtt_ms = 35.0

            sent = int(raw.get("conns_total", 1))
            recv = sent - int(raw.get("conns_failed", 0))

            # Unsupervised Online Prediction-Residual Anomaly Engine
            z_score = 0.0
            route_anomaly = False
            prev_pred = self._pending_predictions.get(slot_id)
            if prev_pred is not None:
                pred_rtt, pred_std = prev_pred
                residual = abs(rtt_ms - pred_rtt)
                z_score = residual / max(1.0, pred_std)
                prev_ewma = self._ewma_z.get(slot_id, 0.0)
                ewma_z = 0.25 * z_score + 0.75 * prev_ewma
                self._ewma_z[slot_id] = ewma_z
                if z_score >= 3.0 or ewma_z >= 2.5:
                    route_anomaly = True
                    logger.warning(
                        f"[{slot_id} / Port {port}] ⚠️ Unsupervised Route Anomaly: z={z_score:.2f} "
                        f"(Actual RTT: {rtt_ms:.1f}ms, Predicted: {pred_rtt:.1f}ms ± {pred_std:.1f}ms)"
                    )
            else:
                ewma_z = 0.0

            obs = Observation(
                timestamp=now,
                rtt_ms=rtt_ms,
                received=max(1, recv),
                sent=max(1, sent),
                stale=False,
            )

            forecast: ForecastResult | None = self.engine.observe(slot_id, obs)

            slot_status = "COLLECTING"
            deg_prob = 0.0
            loss_prob = 0.0
            jitter_ms = 0.0
            pred_h1_rtt = None
            pred_h1_std = None

            if forecast is not None and forecast.rtt_ms:
                pred_h1_rtt = forecast.rtt_ms[0]
                pred_h1_std = forecast.rtt_std_ms[0] if forecast.rtt_std_ms else 1.5
                if pred_h1_rtt is not None and pred_h1_std is not None:
                    self._pending_predictions[slot_id] = (pred_h1_rtt, pred_h1_std)

                if forecast.degradation_probability:
                    deg_prob = float(forecast.degradation_probability[0])
                if forecast.loss_probability:
                    loss_prob = float(forecast.loss_probability[0])
                if forecast.jitter_ms and forecast.jitter_ms[0] is not None:
                    jitter_ms = float(forecast.jitter_ms[0])

                if route_anomaly or deg_prob >= self.degradation_threshold:
                    slot_status = "DEGRADED" if not route_anomaly else "ROUTE_ANOMALY"
                    logger.warning(
                        f"[{slot_id} / Port {port}] High degradation predicted: p={deg_prob:.2f} (Loss: {loss_prob:.3f}, RTT: {rtt_ms:.1f}ms)!"
                    )
                elif deg_prob >= 0.40 or z_score >= 2.0:
                    slot_status = "UNSTABLE"
                else:
                    slot_status = "HEALTHY"

            results[slot_id] = {
                "port": port,
                "online": True,
                "status": slot_status,
                "rtt_ms": round(rtt_ms, 2),
                "predicted_rtt_ms": round(pred_h1_rtt, 2) if pred_h1_rtt is not None else None,
                "residual_z_score": round(z_score, 2),
                "ewma_z_score": round(ewma_z, 2),
                "route_anomaly": route_anomaly,
                "jitter_ms": round(jitter_ms, 2),
                "loss_probability": round(loss_prob, 4),
                "degradation_probability": round(deg_prob, 4),
                "conns_active": raw.get("conns_active", 0),
                "conns_failed": raw.get("conns_failed", 0),
                "last_poll_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            }

        payload = {
            "timestamp": now,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "active_instances": self.engine.active_instances(),
            "slots": results,
        }

        try:
            self.output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.debug(f"Failed to write live status: {exc}")

        return payload

    def run_forever(self) -> None:
        logger.info(
            f"Starting AI Network Health Bridge (Ports: {self.ports}, Interval: {self.poll_interval}s)..."
        )
        while True:
            try:
                self.poll_once()
            except Exception as exc:
                logger.error(f"Error during poll: {exc}")
            time.sleep(self.poll_interval)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Real-time Network Health Bridge")
    parser.add_argument(
        "--checkpoint",
        default="networkhealth_artifacts/model.pt",
        help="Path to trained TRM-MoE checkpoint",
    )
    parser.add_argument(
        "--ports",
        default="8880,8881,8882,8883",
        help="Comma-separated proxy ports to monitor",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.5,
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.70,
        help="Degradation probability alarm threshold",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once and print output instead of looping",
    )
    args = parser.parse_args()

    port_list = [int(p.strip()) for p in args.ports.split(",") if p.strip().isdigit()]
    bridge = NetworkHealthBridge(
        checkpoint_path=args.checkpoint,
        ports=port_list,
        poll_interval=args.interval,
        degradation_threshold=args.threshold,
    )

    if args.once:
        data = bridge.poll_once()
        print(json.dumps(data, indent=2))
        return 0

    bridge.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
