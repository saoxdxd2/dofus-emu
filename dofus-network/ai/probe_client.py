"""Ask the live client what it actually contains.

Written because a reader returning nulls is indistinguishable from a character that
is not in the game. The snapshot said `map_id: null, character: null` while the
character was standing on Astrub 4,-21 — so the agent believed there was nothing to
act on, and every diagnosis built on that reading was wrong.

This sends `debug.probe`, which returns the keys of the client's own objects and a
handful of candidate values. Read it, then fix the reader against reality instead of
against a guess about field names.

    python ai/probe_client.py
    python ai/probe_client.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.bridge import bridge_port, reachable  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print the raw payload")
    ap.add_argument("--instance", default=None)
    args = ap.parse_args(argv)

    port = bridge_port()
    if not reachable():
        print("no bridge answering on %d — is the app running?" % port)
        return 1

    import urllib.request
    payload = {"id": "probe", "ts": 0, "tool": "debug.probe", "args": {},
               "source": "probe"}
    if args.instance:
        payload["target_instance"] = args.instance
    req = urllib.request.Request(
        "http://127.0.0.1:%d/directive" % port,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print("probe failed: %s" % exc)
        return 1

    if args.json:
        print(raw)
        return 0

    try:
        outer = json.loads(raw)
    except ValueError:
        print(raw[:800])
        return 1
    result = outer.get("result") or {}
    if not result.get("ok"):
        print("client refused the probe:", result.get("error"))
        print("(debug.probe exists only after the client script is reloaded — "
              "restart the app)")
        return 1
    data = result.get("data") or {}
    print("target:", outer.get("target"))
    print("\ncandidates:")
    for key, value in (data.get("candidates") or {}).items():
        print("  %-34s %s" % (key, value))
    for section in ("gui_keys", "playerData_keys", "character_keys",
                    "characteristics_keys", "iso_keys", "dofus_keys"):
        value = data.get(section)
        if value is None:
            print("\n%s: absent" % section)
        elif isinstance(value, list):
            print("\n%s (%d): %s" % (section, len(value), ", ".join(map(str, value))))
        else:
            print("\n%s: %s" % (section, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
