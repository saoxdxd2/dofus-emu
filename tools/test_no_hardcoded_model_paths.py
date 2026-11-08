"""Guard against hardcoded model artifact paths in the Python agent.

`command_shadow.py`, `planner_nn.py`, `foresight.py` and related modules bake
absolute model paths into source. A different install location, a renamed model
directory, or a retrained artifact breaks them silently.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_model_paths.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


checks = [
    ("dofus-network/agent/command_shadow.py",
     'DEFAULT_MODEL = REPO_ROOT / "models" / "command_encoder30" / "heads.pt"',
     "command shadow model path"),
    ("dofus-network/agent/planner_nn.py",
     'DEFAULT_MODEL = REPO_ROOT / "models" / "tool_policy.onnx"',
     "planner NN model path"),
    ("dofus-network/agent/foresight.py",
     'MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "foresight_v1"',
     "foresight model path"),
]
for rel_path, literal, name in checks:
    path = REPO / rel_path
    if not path.exists():
        ok(f"{rel_path} absent (check skipped)")
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    if literal in text:
        fail(f"{rel_path} hardcodes {name} ({literal}) — use env/config instead")
    else:
        ok(f"{name} not hardcoded in {rel_path}")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded model path(s) still present")
    sys.exit(1)
print("no hardcoded model paths detected")
sys.exit(0)