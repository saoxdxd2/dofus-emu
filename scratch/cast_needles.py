"""Needle uniqueness for the castable mutations (page + solver)."""
from pathlib import Path

PAGE = Path("../scripts/mobile-disguise.js").read_text(encoding="utf-8")
SOLV = Path("combat/battle_solver.py").read_text(encoding="utf-8")

PAIRS = [
    ("page: throw is a refusal", PAGE,
     '                        castable = null;',
     '                        castable = false;'),
    ("page: reason not recorded", PAGE,
     '                        castableError = String((eCast && eCast.message) || eCast);',
     '                        castableError = null;'),
    ("page: error not in payload", PAGE,
     '                        castable_error: castableError,',
     '                        castable_error: null,'),
    ("page: answer discarded", PAGE,
     '                        castable = myId !== null && !!fm.canCastThisSpell(myId, spellId);',
     '                        castable = myId !== null ? null : false;'),
    ("solver: unknown is a veto", SOLV,
     '    return True if value is None else bool(value)',
     '    return bool(value)'),
    ("solver: everything offered", SOLV,
     '    return True if value is None else bool(value)',
     '    return True'),
]

bad = 0
for label, src, old, new in PAIRS:
    n = src.count(old)
    if n != 1:
        bad += 1
    print("%s %-30s %d match(es)" % ("ok " if n == 1 else "BAD", label, n))
print("not unique:", bad)