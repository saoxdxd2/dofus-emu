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

from agent.serve import main  # noqa: E402

if __name__ == "__main__":
    argv = list(sys.argv[1:])
    if "--brain" not in argv:
        argv.append("--brain")
    raise SystemExit(main(argv))
