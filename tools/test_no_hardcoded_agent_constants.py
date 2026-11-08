"""Guard against hardcoded agent constants in `dofus-network/agent/autopilot.py`.

Many timing, budget and threshold values are baked into the code (300 s session
close, 64 MB log rotation, 4 s observation freshness, 20 s farm idle, etc.).
While each was measured once, they are not configuration — a different machine,
network or client build needs different values. They should come from
`ai_settings.ini`, `network.ini` or environment variables so an operator can
tune without editing code.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_agent_constants.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUTOPILOT = REPO / "dofus-network" / "agent" / "autopilot.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = AUTOPILOT.read_text(encoding="utf-8", errors="replace")

# Timing thresholds that should be configurable.
timing = [
    ("SESSION_CLOSE_TIMEOUT_S = 300", "session close timeout"),
    ("OBS_FRESH_MS = 4000.0", "observation freshness"),
    ("FARM_IDLE_S = 20.0", "farm idle threshold"),
    ("CROSSING_IN_FLIGHT_S = 30.0", "crossing in-flight window"),
    ("PROMISED_SETTLE_WINDOW_S = 6.0", "promised settle window"),
    ("PENDING_CREDIT_STEPS = 12", "pending credit steps"),
    ("WAIT_POLL_S = 0.35", "wait poll interval"),
    ("MAX_CONSECUTIVE_WAITS = 40", "max consecutive waits"),
    ("DEAD_WINDOW_LIMIT = 4", "dead window limit"),
    ("QUEST_DATA_WAIT_LIMIT = 3", "quest data wait limit"),
    ("RETIRE_LIMIT = 3", "retire limit"),
    ("RETIRE_REPEAT_LIMIT = 3", "retire repeat limit"),
    ("ESCALATE_TRIES = 3", "escalate tries"),
    ("TUTORIAL_HOLD_S = 180", "tutorial hold"),
    ("READ_STREAK_LIMIT = 6", "read streak limit"),
    ("DEAD_TALK_TRIES = 2", "dead talk tries"),
    ("NAV_DECLINE_LIMIT = 12", "nav decline limit"),
]
for literal, name in timing:
    if literal in text:
        fail(f"autopilot.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

# Log rotation size.
if "LOG_MAX_BYTES = 64 * 1024 * 1024" in text:
    fail("autopilot.py hardcodes LOG_MAX_BYTES = 64 MiB — make configurable")
else:
    ok("LOG_MAX_BYTES not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded agent constant(s) still present")
    sys.exit(1)
print("no hardcoded agent constants detected")
sys.exit(0)