"""Entry point for the single-process brain daemon.

The host used to call ``run_autopilot.py`` once per instance. It now starts this
script once, then drives it through ``POST /brain/start`` and ``POST /brain/stop``
on port 8895. The script puts the package directory on ``sys.path`` first for the
same reason ``run_autopilot.py`` does: the embedded runtime's ``_pth`` cannot
resolve ``..\\dofus-network`` from the install root.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Before ANY import that prints: the host starts this process with a redirected
# stdout and then may exit while the daemon keeps running. After that the pipe is
# closed and every write raises OSError(EINVAL), which used to abort a start
# inside `_base_policy`'s startup print. Measured 2026-11-13 -- see
# agent/console_guard.py for the traceback and the symptom it produced.
from agent.console_guard import install as _install_console_guard  # noqa: E402

_install_console_guard()

# Mint this session's run token before anything is spawned, so every child inherits it and the
# network pipeline can tell this run's helpers from a leftover that outlived its parent
# (net/orchestrator.py; the 2026-11-04 incident was a 50-minute-old brain answering :8891).
# Optional on purpose: the Kaggle toolkit mirrors this file into a directory without `net/`, and a
# missing pipeline must degrade to "no token", not to a brain that cannot start at all.
try:
    from net.orchestrator import run_token as _run_token  # noqa: E402
    RUN_TOKEN = _run_token()
except Exception as _exc:                                  # noqa: BLE001 - pipeline absent
    RUN_TOKEN = ""
    print("[run_brain] no net.orchestrator, running without a session token: %s" % _exc)

from agent.serve import main  # noqa: E402

if __name__ == "__main__":
    argv = list(sys.argv[1:])
    if "--brain" not in argv:
        argv.append("--brain")
    raise SystemExit(main(argv))
