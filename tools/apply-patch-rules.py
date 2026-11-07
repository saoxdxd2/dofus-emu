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
    """Patch a bundle once, and patching it twice changes nothing.

    Measured 2026-11-04 on build 3.3.11: 23/23 rules anchor on the vendor bundle, and
    running the same rules over the *result* applies 4 of them again -- rules 1, 2 and
    3 are boundary rules whose replacement still contains their own start marker, and
    rule 0's replacement contains its needle. A second adoption therefore wraps those
    callbacks a second time, which is the shape of "Callback was already called".
    So a rule is skipped when its effect is already in the text -- but only for a
    rule that can produce that state itself. Rule 10's replacement is a snippet the
    vendor bundle already contains, and a blunt "effect present, skip" quietly
    dropped it from the first pass (23/23 became 22/23). The guard belongs to the
    rule whose replacement still holds its own anchor, because that is the only rule
    that can re-apply to its own output.
    """
    matched: list[str] = []
    skipped: list[str] = []
    for r in rules:
        rid = r["id"]
        if r["kind"] == "boundary":
            if r["start"] in r["replacement"] and r["replacement"] in js:
                skipped.append("%s: already applied" % rid)
                continue
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
            if r["old"] in r["new"] and r["new"] in js:
                skipped.append("%s: already applied" % rid)
                continue
            if old not in js:
                skipped.append("%s: needle missing" % rid)
                continue
            js = js.replace(old, r["new"], 1)
            matched.append(str(rid))
    return js, matched, skipped


def classify(js: str, rules: list[dict]) -> list[dict]:
    """Per rule: is its anchor there, is its effect already there, or has the target moved?

    This is the question the update chain kept answering wrongly. A rule that
    matches text is not a rule that is *needed*, and a rule whose anchor has moved
    is not a rule that is broken -- it is a rule the bundle no longer describes.
    Measured 2026-11-04 on build 3.3.11: 23/23 matched and the patched client still
    double-fired its series callback, which is how "all rules matched" came to mean
    nothing.

    `reapply_unsafe` is its own finding: a replace rule whose replacement *contains*
    its needle applies again on every adoption, so a patched bundle that is patched
    twice grows a second guard instead of staying patched.
    """
    rows = []
    for r in rules:
        rid, kind = r.get("id"), r.get("kind")
        if kind == "boundary":
            anchor = js.find(r["start"]) >= 0 and js.find(r["end"], js.find(r["start"])) >= 0
            effect = r["replacement"] in js
            unsafe = r["start"] in r["replacement"]
        else:
            anchor = r["old"] in js
            effect = r["new"] in js
            unsafe = r["old"] in r["new"]
        if anchor and effect:
            state = "ambiguous" if kind != "boundary" else "applies"
        elif anchor:
            state = "applies"
        elif effect:
            state = "already-applied"
        else:
            state = "moved"
        rows.append({"id": rid, "kind": kind, "core": int(r.get("core") or 0),
                     "anchor": anchor, "effect": effect, "state": state,
                     "reapply_unsafe": unsafe})
    return rows


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check = "--check" in sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    src = Path(args[0])
    js = src.read_text(encoding="utf-8", errors="surrogateescape")
    raw = len(src.read_bytes())
    if check:
        rows = classify(js, load_rules())
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        print(json.dumps({"bundle": str(src), "bytes": raw, "chars": len(js),
                          "rules": len(rows), "states": counts,
                          "reapply_unsafe": [row["id"] for row in rows if row["reapply_unsafe"]],
                          "rows": rows}, ensure_ascii=False, indent=1))
        # A rule whose anchor moved means this file describes a different build than
        # the one on disk -- that is a verdict about the rules, not about the bundle.
        return 0 if not counts.get("moved") else 1
    dst = Path(args[1]) if len(args) > 1 else src.with_suffix(".patched.js")
    out, matched, skipped = apply(js, load_rules())
    dst.write_text(out, encoding="utf-8", errors="surrogateescape")
    print("matched: %s" % ", ".join(matched))
    for s in skipped:
        print("skipped: %s" % s)
    # `len(js)` is UTF-16 code units on every other Python path in this repo, and a
    # bundle of this size is 35,195 units away from its own byte count.
    print("in %d bytes (%d chars) -> out %d chars -> %s" % (raw, len(js), len(out), dst))
    return 0 if not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
