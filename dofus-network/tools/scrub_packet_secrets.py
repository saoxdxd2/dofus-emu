"""Remove stored session tickets from the recorded corpora.

The page's map-transition ring used to copy every scalar packet argument, and
``AuthenticationTicketMessage`` carries the live session ticket. The source is
fixed (the page filters credential names, and ``agent.observe`` scrubs any
snapshot from an older build), but files written before the fix still hold it.
This rewrites those files in place, touching only the ticket value and leaving
every other byte as it was.

Dry run by default; ``--write`` rewrites atomically per file. Run it while no
autopilot loop is writing the files (a stopped slot, not a live one).
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DofusFarm"

DEFAULT_PATTERNS = [
    str(APP_DATA / "autopilot*.jsonl"),
    str(REPO_ROOT / "trajectories" / "*.jsonl"),
    str(REPO_ROOT / "data" / "**" / "*.jsonl"),
]

# Keep the key and its spacing; replace only the value. A ticket is a short
# alphanumeric session key, so it never needs escape handling beyond the
# generic JSON string body. An already-redacted value is left alone, so the
# tool is safe to re-run and its count is the number of live secrets.
TICKET = re.compile(rb'("ticket"\s*:\s*)"(?!\[redacted\])(?:[^"\\]|\\.)*"')
REPLACEMENT = rb'\1"[redacted]"'


def scrub(path: Path, write: bool) -> int:
    try:
        blob = path.read_bytes()
    except OSError as exc:
        print("  skip %s (%s)" % (path, exc))
        return 0
    cleaned, count = TICKET.subn(REPLACEMENT, blob)
    if not count:
        return 0
    print("  %6d ticket(s) in %s" % (count, path))
    if write:
        tmp = path.with_name(path.name + ".scrub.tmp")
        try:
            tmp.write_bytes(cleaned)
            os.replace(tmp, path)
        except OSError as exc:
            print("  LOCKED %s (%s); left as it was" % (path, exc))
            try:
                tmp.unlink()
            except OSError:
                pass
            return -count
    return count


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="rewrite the files; without it this is a dry run")
    parser.add_argument("--paths", nargs="*", default=None,
                        help="glob patterns (default: the recorded corpora)")
    args = parser.parse_args(argv)

    patterns = args.paths or DEFAULT_PATTERNS
    seen, total, locked = set(), 0, 0
    print("scanning %d pattern(s)%s" % (len(patterns), "" if args.write else " (dry run)"))
    for pattern in patterns:
        for name in sorted(glob.glob(pattern, recursive=True)):
            path = Path(name)
            if path in seen or not path.is_file() or path.name.endswith(".scrub.tmp"):
                continue
            seen.add(path)
            count = scrub(path, args.write)
            if count < 0:
                locked += -count
            else:
                total += count
    print("%d ticket value(s) %s" % (total, "removed" if args.write else "found"))
    if locked:
        print("%d ticket value(s) still in files another process holds open" % locked)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
