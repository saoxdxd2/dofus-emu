"""Poll one Kaggle kernel through the SOCKS wrapper until it finishes."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time


def run(ref: str, out: pathlib.Path, interval: int, deadline: int) -> int:
    started = time.monotonic()
    out.parent.mkdir(parents=True, exist_ok=True)
    history = []
    while time.monotonic() - started < deadline:
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve().parent / "kaggle_tunnel.py"),
             "--", "kernels", "status", ref],
            capture_output=True, text=True, timeout=60)
        status = (result.stdout + result.stderr).strip().lower()
        row = {"at": round(time.time(), 3), "returncode": result.returncode, "status": status}
        history.append(row)
        out.write_text(json.dumps({"ref": ref, "history": history}, indent=1), encoding="utf-8")
        if "complete" in status:
            return 0
        if "error" in status or "cancel" in status:
            return 2
        time.sleep(interval)
    return 3


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1], pathlib.Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])))
