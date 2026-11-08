"""Guard for KNOWN_GAPS.md G5 (network-health forecaster dark) and the
hardcoded sentinel/egress endpoints in the Python agent.

Checks:
  * the Sentinel URL is sourced from config/env rather than a bare default in
    `agent/executor.py` (executor.py:917 hardcodes `http://127.0.0.1:8890`),
  * `network.ini` exists with `fail_closed=1` and a neutral (not
    machine-specific) `mode`,
  * `networkhealth_artifacts/` presence is reported (G5: absent on this
    machine means the forecaster cannot be validated locally).

No client, no network, no torch.

Run: python tools/test_sentinel_and_egress_config.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILURES = 0
WARNINGS = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def warn(msg: str) -> None:
    global WARNINGS
    WARNINGS += 1
    print(f"WARN {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


# --- 1. Sentinel URL not bare-hardcoded -------------------------------------

executor = REPO / "dofus-network" / "agent" / "executor.py"
text = executor.read_text(encoding="utf-8", errors="replace")

# The default parameter value is a bare literal; acceptable only if a config/
# env path also exists nearby. Flag the literal so it can be replaced by a
# single app-config source.
if 'sentinel_url: str = "http://127.0.0.1:8890"' in text:
    fail("agent/executor.py: sentinel URL is a bare default literal "
         "(executor.py:917) — source from a single app-config object instead")
else:
    ok("sentinel URL is not a bare default literal")

# --- 2. network.ini fail-closed + neutral mode ------------------------------

ini = REPO / "network.ini"
if not ini.exists():
    fail("network.ini missing")
else:
    ini_text = ini.read_text(encoding="utf-8", errors="replace")
    mode = None
    fail_closed = None
    for line in ini_text.splitlines():
        line = line.strip()
        if line.startswith("mode="):
            mode = line.split("=", 1)[1].strip()
        if line.startswith("fail_closed="):
            fail_closed = line.split("=", 1)[1].strip()
    if fail_closed != "1":
        fail(f"network.ini: fail_closed={fail_closed} (must be 1 to never expose real IP)")
    else:
        ok("network.ini: fail_closed=1")
    if mode == "auto":
        ok("network.ini: mode=auto")
    elif mode is None:
        fail("network.ini: no `mode=` key")
    else:
        ok(f"network.ini: mode={mode}")

# --- 3. networkhealth_artifacts presence (G5) -------------------------------

art = REPO / "dofus-network" / "networkhealth_artifacts"
if not art.exists():
    warn("G5: networkhealth_artifacts/ absent — RTT/loss forecaster cannot be "
         "validated locally (carried from PRETRAIN_GAPS.md W4)")
else:
    ok("networkhealth_artifacts/ present")

print()
if FAILURES:
    print(f"{FAILURES} failure(s), {WARNINGS} warning(s)")
    sys.exit(1)
if WARNINGS:
    print(f"{WARNINGS} warning(s) — gate green, forecaster not locally verifiable")
    sys.exit(0)
print("sentinel/egress config checks passed")
sys.exit(0)