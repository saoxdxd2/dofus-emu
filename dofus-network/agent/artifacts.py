"""Versioned rollback for learned artifacts that are a single file.

The fight calibrator already versions itself (`combat/online_learn.py --rollback`);
the move head and the tool stack write one JSON in place, so a bad promotion had no
way back. This is the shared rule, and only that rule:

* before a writer replaces an existing artifact, it calls `archive_previous`, which
  keeps the old bytes beside it as `<name>.previous.json`;
* `restore_previous` puts those bytes back and says what it did.

Nothing here decides *when* to roll back. The gates decide, and the caller records
the decision in its own ledger; a rollback is a reversible file copy, not a policy.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional


def previous_path(path: Path) -> Path:
    """`tool_stack.json` -> `tool_stack.previous.json` (same directory)."""
    path = Path(path)
    return path.with_name(path.stem + ".previous" + path.suffix)


def _atomic_copy(source: Path, target: Path) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def archive_previous(path: Path) -> Optional[Path]:
    """Keep the current artifact as the rollback target; None when there is none."""
    path = Path(path)
    if not path.is_file():
        return None
    target = previous_path(path)
    _atomic_copy(path, target)
    return target


def publish(source: Path, target: Path) -> Path:
    """Replace `target` with `source`, keeping the old `target` recoverable."""
    archive_previous(target)
    _atomic_copy(Path(source), Path(target))
    return Path(target)


def restore_previous(path: Path) -> Dict[str, Any]:
    """Copy the previous artifact over the current one; never invent one."""
    path = Path(path)
    previous = previous_path(path)
    if not previous.is_file():
        return {"restored": False, "reason": "no_previous", "artifact": str(path)}
    _atomic_copy(previous, path)
    return {"restored": True, "artifact": str(path), "previous": str(previous)}
