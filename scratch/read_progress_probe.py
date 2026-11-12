"""Drive the real autopilot loop over a scripted policy, with a frozen or moving world."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "dofus-network"
sys.path.insert(0, str(ROOT))

import agent.autopilot as _gm
from agent.autopilot import Autopilot
from agent.runtime import ToolCall

GOAL = "do all the quests"


class _Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def perf_counter(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(float(seconds), 0.0)

    def time(self):
        return 1_700_000_000.0 + self.t


def drive(script, change_on=(), limit=14):
    """`script` is the policy's call cycle; `change_on` are 1-based execute indices
    whose snapshot moves the character, so the verifier scores them `advanced`."""
    tmp = Path(tempfile.mkdtemp())
    clock = _Clock()

    class _Pol:
        def __init__(self):
            self.calls = 0

        def choose(self, goal, obs, tools):
            self.calls += 1
            if self.calls > limit:
                (tmp / "STOP").write_text("stop", encoding="utf-8")
            tool, args = script[(self.calls - 1) % len(script)]
            return ToolCall(tool, dict(args), goal)

        def note_result(self, *a, **k):
            return None

    class _Res:
        def __init__(self, data):
            self.ok = True
            self.error = None
            self.data = data

        def as_dict(self):
            return {"data": self.data}

    class _RT:
        def __init__(self):
            self.obs = {"in_world": True, "map_id": 147062786, "cell": 263,
                        "objectives_completed": 20, "in_dialog": False,
                        "tutorial": {"active": False}}
            self.policy = _Pol()
            self.instance = "unit"
            self.executor = self
            self.executed = 0

        def execute(self, tool, args, **kw):
            self.executed += 1
            if self.executed in change_on:
                self.obs["cell"] = 263 + self.executed
            return _Res({"response": {"result": {"ok": True}},
                         "snapshot": dict(self.obs)})

        def current_observation(self):
            return dict(self.obs), 5.0, "cache"

        def observe(self, obs, source="live"):
            return None

        def note_observation(self, obs):
            return None

    class _Think:
        def propose(self, goal, obs, recent, hint):
            tool, args = script[0]
            return ToolCall(tool, dict(args), goal)

    rt = _RT()
    ap = Autopilot(runtime=rt, goals=[GOAL], log_path=tmp / "ap.jsonl",
                   stop_file=tmp / "STOP", max_steps=0, dry_run=False,
                   preflight=False, minutes=0, interval=0.0)
    ap.controller.state_dir = tmp
    ap.thinking = _Think()
    real_time, _gm.time = _gm.time, clock
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ap._run()
    finally:
        _gm.time = real_time
    out = buf.getvalue()
    rows = [json.loads(l) for l in
            (tmp / "ap.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    counters = (dict(ap._verdict_counts), ap._world_changes, ap._read_answers)
    with contextlib.redirect_stdout(io.StringIO()):
        report = ap.summary("probe")
    shutil.rmtree(tmp, ignore_errors=True)
    return out, rows, counters, report


if __name__ == "__main__":
    say = ("chat.say", {"text": "hi"})
    read = ("quest.list", {})
    out, rows, counters, report = drive([say, read])
    print("=== interleaved ===")
    print(out)
    print("counters", counters)
    print("summary", {k: report[k] for k in ("verdicts", "world_changes", "read_answers")})
    out2, _, c2, r2 = drive([say], change_on=(2,), limit=4)
    print("=== one real change in the middle ===")
    print(out2)
    print("counters", c2)
    print("summary", {k: r2[k] for k in ("verdicts", "world_changes", "read_answers")})