"""Apply tools/patch-rules.json to a bundle and report what matched.

This is the reference implementation of the runtime patcher in
desktop/ClientBundle.cs. It exists so the JSON rules can be verified against a
real bundle without building the app:

    python tools/apply-patch-rules.py desktop/client/www/build/script.js.bak /tmp/patched.js

then diff /tmp/patched.js against the current (build-time patched) script.js.
If they are identical, the JSON rules are a faithful copy of patch-engine.js.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_rules() -> list[dict]:
    return json.loads((ROOT / "tools" / "patch-rules.json").read_text(encoding="utf-8"))["rules"]


def apply(js: str, rules: list[dict]) -> tuple[str, list[str], list[str]]:
    matched: list[str] = []
    skipped: list[str] = []
    for r in rules:
        rid = r["id"]
        if r["kind"] == "boundary":
            start = js.find(r["start"])
            if start < 0:
                skipped.append("%s: start marker missing" % rid)
                continue
            end = js.find(r["end"], start)
            if end < 0:
                skipped.append("%s: end marker missing" % rid)
                continue
            end += len(r["end"])
            span = end - start
            if span > int(r.get("maxLen") or 10 ** 9):
                skipped.append("%s: span %d chars, refused" % (rid, span))
                continue
            js = js[:start] + r["replacement"] + js[end:]
            matched.append(str(rid))
        else:
            old = r["old"]
            if old not in js:
                skipped.append("%s: needle missing" % rid)
                continue
            js = js.replace(old, r["new"], 1)
            matched.append(str(rid))
    return js, matched, skipped


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".patched.js")
    js = src.read_text(encoding="utf-8", errors="surrogateescape")
    out, matched, skipped = apply(js, load_rules())
    dst.write_text(out, encoding="utf-8", errors="surrogateescape")
    print("matched: %s" % ", ".join(matched))
    for s in skipped:
        print("skipped: %s" % s)
    print("in %d bytes -> out %d bytes -> %s" % (len(js), len(out), dst))
    return 0 if not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
