"""Needle uniqueness for the LDATA mutations, before they go in the driver."""
from pathlib import Path

SRC = Path("combat/live_data.py").read_text(encoding="utf-8")

PAIRS = [
    ("read the empty list",
     '    log = board.get("battle_log")\n    if isinstance(log, list) and log:\n'
     '        return _actions_from_log(log)',
     '    log = board.get("battle_log")\n    if False:\n'
     '        return _actions_from_log(log)'),
    ("no fallback",
     '    return _actions_from_last_actions(board.get("last_actions") or [],\n'
     '                                      board.get("round"))',
     '    return []'),
    ("another target",
     '        if entry["target_id"] != row.get("target") or cast_round != rounds:',
     '        if cast_round != rounds:'),
    ("earlier round",
     '        if entry["target_id"] != row.get("target") or cast_round != rounds:',
     '        if entry["target_id"] != row.get("target"):'),
    ("any source",
     '        prior = last_cast.get(row.get("source"))',
     '        prior = next(iter(last_cast.values()), None)'),
    ("second row replaces first",
     '        entry["dealt"] = (entry["dealt"] or 0.0) + value',
     '        entry["dealt"] = value'),
    ("damage_rows never counted",
     '        ATTRIBUTION["damage_rows"] += 1\n'
     '        prior = last_cast.get(row.get("source"))',
     '        prior = last_cast.get(row.get("source"))'),
    ("log_index dropped",
     '                "log_index": index,',
     '                "log_index": None,'),
    ("verdict filed under the run",
     '                    outcomes[(run, int(fight_id))] = record',
     '                    outcomes[(run, 0)] = record'),
    ("fight id dropped on read",
     '            "outcome": (outcomes.get((run, fight_id)) or {}).get("outcome"),',
     '            "outcome": (outcomes.get((run, 0)) or {}).get("outcome"),'),
    ("attributed never counted",
     '                    ATTRIBUTION["attributed"] += 1',
     '                    pass'),
]

bad = 0
for label, old, new in PAIRS:
    n = SRC.count(old)
    flag = "ok " if n == 1 else "BAD"
    if n != 1:
        bad += 1
    print("%s %-32s %d match(es)" % (flag, label, n))
print("needles that are not unique:", bad)