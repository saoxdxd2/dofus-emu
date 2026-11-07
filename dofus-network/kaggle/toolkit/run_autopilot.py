"""Entry point for the GUI: run the autonomous loop from a known script path.

Why a script and not ``python -m agent.autopilot``
-------------------------------------------------
The embedded runtime ships a ``python311._pth`` that adds ``..\\dofus-network``,
which resolves to a directory that does not exist, so ``-m`` never finds the
package from the install root. Running this file instead puts its own directory on
``sys.path`` first, which works from any working directory and needs no
environment setup from the host.

The host passes ordinary CLI flags; nothing here parses them, so the argument
surface stays exactly `agent.autopilot`'s.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from agent.autopilot import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
