"""One command from a TPU checkpoint to a sim-sourced fight calibrator.

    python -m combat.encounter.calibrate --data encounters.npz \
        --params encounter_selfplay.pkl --out models/fight_calibrator.sim.json

This is deliberately *not* the live calibrator path. It writes ``source=sim``
and refuses to replace ``models/fight_calibrator.json`` unless the caller
passes ``--install`` and the ECE gate passes. The live guard must stay on the
bootstrap envelope until the M3 live gate has real fight outcomes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from combat.encounter.labels import export
from combat.fight_estimate import calibration_report, fit_calibrator

DEFAULT_LIVE = Path(__file__).resolve().parent.parent.parent / "models" / "fight_calibrator.json"


def run(args: argparse.Namespace) -> int:
    labels = Path(args.labels)
    export(argparse.Namespace(data=args.data, params=args.params,
                             out=str(labels), samples=args.samples,
                             seed=args.seed))
    rows: List[Dict[str, Any]] = []
    with labels.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    model = fit_calibrator(rows)
    if model is None:
        raise SystemExit("cannot calibrate: need both wins and losses, >=20 rows")
    report = calibration_report(model, rows)
    passed = report["ece"] is not None and float(report["ece"]) <= args.max_ece
    model.source = "sim"
    model.metrics = {**report, "max_ece": args.max_ece,
                     "gate_passed": passed}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model.to_dict(), indent=1), encoding="utf-8")
    print("sim calibrator:", out)
    print("rows %d wins %d losses %d | ECE %s | Brier %s | gate %s"
          % (report["n"], report["wins"], report["losses"], report["ece"],
             report["brier"], "pass" if passed else "FAIL"))
    if not passed:
        print("The gate failed: do not install this calibrator.")
        return 1
    if args.install:
        if out.resolve() == DEFAULT_LIVE.resolve():
            print("refusing to install a sim calibrator at the live path")
            return 2
        DEFAULT_LIVE.write_text(out.read_text(encoding="utf-8"),
                                encoding="utf-8")
        print("installed at", DEFAULT_LIVE)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--params", required=True)
    p.add_argument("--labels", default="trajectories/sim_fight_outcomes.jsonl")
    p.add_argument("--out", default="models/fight_calibrator.sim.json")
    p.add_argument("--samples", type=int, default=2048)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--max-ece", type=float, default=0.10)
    p.add_argument("--install", action="store_true",
                   help="copy to models/fight_calibrator.json when the gate passes")
    return p


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
