"""Run the agent loop from any interpreter, including the embedded runtime.

`python -m agent.autopilot` is the normal way in, but the bundled `runtimes\\python`
build ignores `PYTHONPATH` (its `sys.path` is fixed at compile time), so `-m` cannot
find the `agent` package from a launcher that does not start in this directory. This
shim puts the repository root on the path itself and forwards argv, which makes the
entry point independent of how the interpreter was built.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.autopilot import main  # noqa: E402  (import after the path fix)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
