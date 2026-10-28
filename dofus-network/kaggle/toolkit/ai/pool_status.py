"""The pool's numbers, in one place, without shell arithmetic.

`manage-proxy.bat` used to count lines with `find /c /v ""`. That works in cmd.exe and
catastrophically does not from Git Bash, where `find` is GNU find and the command walks the
entire drive instead of counting a file. It is not a mistake a batch file can defend against
— PATH is whatever the caller had — so the counting moved here, where it is just python.

    python ai/pool_status.py            human lines
    python ai/pool_status.py --json     for scripts
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = REPO_ROOT.parent
POOL = ROOT / "proxies.txt"
RAW = ROOT / "proxies_raw.txt"
STATE = ROOT / "proxy_pool_state.json"
INI = ROOT / "network.ini"


def count_entries(path: Path) -> int:
    """Lines that look like a proxy, ignoring the manager's own header comments."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle
                       if line.startswith(("http://", "https://", "socks5://")))
    except OSError:
        return 0


def count_lines(path: Path) -> int:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def read_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return {
        "approved": len(data.get("approved") or []),
        "quarantine": len(data.get("quarantine") or {}),
        "dead": len(data.get("dead") or []),
    }


def read_ini() -> dict:
    out = {}
    try:
        for line in INI.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip()
    except OSError:
        pass
    return out


def main(argv=None) -> int:
    cfg = read_ini()
    state = read_state()
    data = {
        "pool_entries": count_entries(POOL),
        "raw_queue": count_lines(RAW),
        "approved": state.get("approved", 0),
        "quarantine": state.get("quarantine", 0),
        "dead": state.get("dead", 0),
        "has_state": bool(state),
        "mode": cfg.get("mode", "?"),
        "socks_port": cfg.get("socks_port", "40000"),
        "fail_closed": cfg.get("fail_closed", "?"),
        "allow_system_warp": cfg.get("allow_system_warp", "0"),
    }

    if "--json" in (argv or sys.argv[1:]):
        print(json.dumps(data, indent=1))
        return 0

    print("  proxies.txt        : %d live entr%s" %
          (data["pool_entries"], "y" if data["pool_entries"] == 1 else "ies"))
    print("  proxies_raw.txt    : %d candidates queued" % data["raw_queue"])
    if data["has_state"]:
        print("  bookkeeping        : %d approved, %d quarantined, %d dead"
              % (data["approved"], data["quarantine"], data["dead"]))
    else:
        print("  bookkeeping        : none yet (the pool has not run)")
    print("  mode               : %s   fail_closed: %s   socks_port: %s"
          % (data["mode"], data["fail_closed"], data["socks_port"]))
    if str(data["allow_system_warp"]).lower() in ("1", "true", "yes"):
        print("  WARNING            : allow_system_warp is on — warp-cli will take over")
        print("                       routing and DNS for the whole PC.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
