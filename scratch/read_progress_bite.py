"""Prove the read-vs-progress checks bite: run them against patched source in memory.

The repo file is never written. Each variant re-imports agent.autopilot from a
patched source string under the real module name.
"""
import importlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "dofus-network"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SRC = (ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8")

GUARD = ('                    if call.tool not in READ_ONLY_TOOLS:\n'
         '                        self._recent.clear()')
SPLIT = ('        if step.verdict == "advanced":\n'
         '            if step.tool in READ_ONLY_TOOLS:\n'
         '                self._read_answers += 1\n'
         '            else:\n'
         '                self._world_changes += 1\n')

PATCHES = {
    "reads clear the streak again": (GUARD, '                    if True:\n'
                                            '                        self._recent.clear()'),
    "nothing clears the streak": (GUARD, '                    if False:\n'
                                         '                        self._recent.clear()'),
    "reads counted as world changes": (SPLIT, '        if step.verdict == "advanced":\n'
                                              '            self._world_changes += 1\n'),
    "the split never reaches summary": ('            "world_changes": self._world_changes,\n'
                                        '            "read_answers": self._read_answers,\n',
                                        '            "world_changes": 0,\n'
                                        '            "read_answers": 0,\n'),
}


def load(variant):
    src = SRC
    if variant is not None:
        needle, repl = PATCHES[variant]
        assert src.count(needle) == 1, (variant, src.count(needle))
        src = src.replace(needle, repl)
    for name in [m for m in sys.modules if m.startswith("agent.")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location("agent.autopilot",
                                                 ROOT / "agent" / "autopilot.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent.autopilot"] = mod
    exec(compile(src, str(ROOT / "agent" / "autopilot.py"), "exec"), mod.__dict__)
    return mod


def run(variant):
    load(variant)
    import read_progress_probe as probe
    importlib.reload(probe)
    say = ("chat.say", {"text": "hi"})
    read = ("quest.list", {})
    out, _, (v, world, reads), rep = probe.drive([say, read])
    out2, _, (v2, world2, reads2), rep2 = probe.drive([say], change_on=(2,), limit=4)
    return {
        "read_between_noops_still_bounded": "no progress: chat.say repeated 3x" in out,
        "noops_before_the_bound": v.get("noop", 0),
        "reads_are_not_world_changes": world == 0 and reads == 1,
        "a_real_change_restarts_the_streak": v2.get("noop", 0) == 4,
        "a_real_change_is_a_world_change": world2 == 1 and reads2 == 0,
        "advanced_equals_the_split": (v.get("advanced", 0) == world + reads
                                      and v2.get("advanced", 0) == world2 + reads2),
        "the_split_reaches_the_report": (
            rep.get("world_changes") == 0 and rep.get("read_answers") == 1
            and rep2.get("world_changes") == 1 and rep2.get("read_answers") == 0),
    }


if __name__ == "__main__":
    base = run(None)
    print("FIXED")
    for k, val in base.items():
        print("   %-38s %s" % (k, val))
    for variant in PATCHES:
        got = run(variant)
        broken = [k for k in base if base[k] is True and got[k] is not True]
        print("%-34s kills: %s" % (variant, broken or "NONE -- CHECK DOES NOT BITE"))