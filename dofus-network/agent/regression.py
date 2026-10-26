"""Offline regression gate for the agent loop — the contract the deleted
smoke_verifier used to enforce (KNOWN_GAPS.md G8).

No client, no network, no torch, no onnxruntime. It runs the REAL code paths:

  * the verifier rule engine (all four verdicts, nested-alias changes, the
    ready_objectives claim-proof, read-only-as-advanced),
  * the schema / capability / feature tool vocabulary (they must be one set,
    and hdv.buy must stay absent and forbidden),
  * the objective-type buckets,
  * the JS page suites, by delegating to ``node tools/test_*.js``,
  * the bundle map-transition resilience rules, by delegating to
    ``node tools/apply-map-resilience.js --check`` (read-only).

Run from ``dofus-network/``:

    python -m agent.regression

Exit code 0 = green. A non-zero exit means a contract moved. This is a gate, not
a substitute for a live session: it proves the loop's pure logic, not that the
game accepted a packet.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent      # .../dofus-network
ROOT = REPO_ROOT.parent                                  # .../dofus-emu

_FAILURES: List[str] = []
_CHECKS = 0


def check(label: str, condition: Any, detail: Any = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if condition:
        return
    text = "%s%s" % (label, (": %s" % detail) if detail else "")
    _FAILURES.append(text)
    print("FAIL %s" % text)


def section(title: str) -> None:
    print("\n== %s ==" % title)


# ---------------------------------------------------------------------------
def test_verdict_vocabulary() -> None:
    from agent import verifier

    section("verifier vocabulary")
    check("VERDICTS are the four labels",
          tuple(verifier.VERDICTS) == ("advanced", "noop", "failed", "blocked"),
          verifier.VERDICTS)
    check("read-only tools are a known set",
          {"state.snapshot", "quest.list", "knowledge.search"} <=
          set(verifier.READ_ONLY_TOOLS),
          sorted(verifier.READ_ONLY_TOOLS))


def _new_verifier():
    from agent.verifier import Verifier
    # A human path that does not exist loads empty and never touches the real
    # %LOCALAPPDATA%\DofusFarm\verdicts_human.jsonl.
    missing = Path(tempfile.gettempdir()) / "dofus_regression_no_human.jsonl"
    missing.unlink(missing_ok=True)
    return Verifier(model_path=None, human_path=missing, veto_threshold=0.0)


def test_verifier_rules() -> None:
    from agent import verifier
    from agent.verifier import changed_keys

    section("verifier rule engine")
    v = _new_verifier()
    check("rules-only with no model", v.model_note.startswith("no verifier model"),
          v.model_note)

    def lab(*a, **k):
        return v.label(*a, **k).verdict

    # a query that returned ok is success by definition
    check("read-only ok => advanced",
          lab("state.snapshot", {}, True, None, {}, {}) == "advanced")

    # a call that moved the cell is progress
    check("cell change => advanced",
          lab("nav.goto", {}, True, None, {"cell": 5}, {"cell": 9}) == "advanced")

    # a call that moved nothing is a noop
    check("no state change => noop",
          lab("nav.goto", {}, True, None, {"cell": 5}, {"cell": 5}) == "noop")

    # nested snapshot shape must be read through the aliases, not flat keys
    check("nested position.map_id change => advanced",
          lab("nav.goto", {}, True, None,
              {"position": {"map_id": 100}}, {"position": {"map_id": 200}}) == "advanced")

    # the claim proof: an objective validated moves off ready_objectives
    check("ready_objectives emptied => advanced",
          lab("quest.advance", {"objective_id": 14070}, True, None,
              {"ready_objectives": [14070]}, {"ready_objectives": []}) == "advanced")

    # failures and the not_mapped veto
    check("not-ok => failed",
          lab("quest.advance", {}, False, "server refused", {}, {}) == "failed")
    check("not_mapped => blocked",
          lab("nav.goto", {}, False, "not_mapped: hdv_ui", {}, {}) == "blocked")

    # the public helper agrees with the internal one
    check("changed_keys is consistent with the rule",
          changed_keys({"cell": 1}, {"cell": 2}) == verifier._changed({"cell": 1}, {"cell": 2}))


def test_verifier_model_contract() -> None:
    from agent import verifier
    from agent.verifier import Verifier, VERDICTS

    section("verifier model contract (G6: head is wired, guard is real)")
    tmp = Path(tempfile.gettempdir())
    none_human = tmp / "dofus_regression_no_human.jsonl"
    width = len(verifier.features("nav.goto", True, None, {"cell": 1}, {"cell": 2}, 10.0))

    def write_baseline(prefer: str) -> Path:
        w = [[0.0] * width for _ in VERDICTS]
        b = [0.0] * len(VERDICTS)
        b[VERDICTS.index(prefer)] = 10.0     # force a confident preference
        path = tmp / "dofus_regression_vb.json"
        path.write_text(json.dumps({"kind": "verifier-baseline", "w": w, "b": b,
                                    "classes": list(VERDICTS),
                                    "metrics": {"val": {"accuracy": 0.99}}}),
                        encoding="utf-8")
        return path

    # (a) a valid baseline artifact loads (the contract accepts its kind)
    v = Verifier(model_path=write_baseline("advanced"), human_path=none_human)
    check("a verifier-baseline json loads", v._session == "baseline", v._session)

    # (b) a wrong-kind json is rejected to rules, no crash
    bad = tmp / "dofus_regression_bad.json"
    bad.write_text(json.dumps({"kind": "not-a-verifier"}), encoding="utf-8")
    vbad = Verifier(model_path=bad, human_path=none_human)
    check("a non-baseline json degrades to rules",
          vbad._session is None
          and vbad.label("state.snapshot", {}, True, None, {}, {}).verdict == "advanced",
          vbad.model_note)

    # (c) THE GUARD: a read-only query is labelled by rules even when a confident
    #     model would say 'failed'. This is the one thing that stops the stale head
    #     (trained on the old "query = noop" labels) from mislabbing a lookup.
    vfail = Verifier(model_path=write_baseline("failed"), human_path=none_human)
    got = vfail.label("state.snapshot", {}, True, None, {}, {})
    check("a read-only query is never model-labelled (stale-head guard)",
          got.verdict == "advanced" and got.source == "rule", (got.verdict, got.source))

    # (d) but an ACTION does defer to a confident model: the head is live for
    #     non-read-only tools, so its accuracy matters — and that accuracy is
    #     unmeasured on real labels (the G6 finding, not a passing claim).
    act = vfail.label("nav.goto", {}, True, None, {"cell": 5}, {"cell": 5})  # rule=>noop
    check("an action defers to the loaded model (head is wired, not orphaned)",
          act.source == "model" and act.verdict == "failed", (act.source, act.verdict))

    for p in (none_human, tmp / "dofus_regression_vb.json", bad):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def test_tool_vocabulary() -> None:
    from agent import schema, features

    section("tool vocabulary (one source of truth)")
    tool_names = [getattr(t, "name", None) or getattr(t, "id", None) for t in schema.TOOLS]
    tool_names = [n for n in tool_names if n]

    caps_path = REPO_ROOT / "data" / "capabilities.json"
    caps_ids: List[str] = []
    if caps_path.exists():
        data = json.loads(caps_path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("tools", data.get("capabilities", []))
        caps_ids = [(r.get("id") or r.get("name")) for r in rows if (r.get("id") or r.get("name"))]

    reg_ids = features.tool_registry()

    check("schema declares 21 tools", len(tool_names) == 21, len(tool_names))
    check("capabilities.json lists 21 tools", len(caps_ids) == 21, len(caps_ids))
    check("features.tool_registry lists the same set as the schema",
          set(reg_ids) == set(tool_names), (sorted(set(reg_ids) ^ set(tool_names))))
    check("capabilities.json matches the schema set",
          set(caps_ids) == set(tool_names), (sorted(set(caps_ids) ^ set(tool_names))))
    check("no duplicate tool ids", len(set(tool_names)) == len(tool_names))
    check("hdv.buy is not in the schema (purchases are forbidden)",
          "hdv.buy" not in tool_names)


def test_forbidden_and_validation() -> None:
    from agent.autopilot import FORBIDDEN_TOOLS
    from agent import schema

    section("safety gates")
    check("the purchase ban is exactly hdv.buy",
          set(FORBIDDEN_TOOLS) == {"hdv.buy"}, sorted(FORBIDDEN_TOOLS))

    ok_unknown, _ = schema.validate_call("definitely.not.a.tool", {})
    check("validate_call rejects an unknown tool", ok_unknown is False, ok_unknown)

    # every real tool must validate against its own spec with no crash
    crash: Optional[str] = None
    for t in schema.TOOLS:
        name = getattr(t, "name", None) or getattr(t, "id", None)
        try:
            schema.validate_call(name, {})
        except Exception as exc:  # noqa: BLE001
            crash = "%s: %s" % (name, exc)
            break
    check("validate_call runs on every declared tool without crashing",
          crash is None, crash)


def test_objective_buckets() -> None:
    from agent import features

    section("objective-type buckets")
    check("talk types are {1, 9}", features.TALK_TYPES == {1, 9}, features.TALK_TYPES)
    check("kill types cover {0, 6, 14, 16}", features.KILL_TYPES == {0, 6, 14, 16},
          features.KILL_TYPES)
    check("fight type is {6}", features.FIGHT_TYPES == {6}, features.FIGHT_TYPES)
    check("discover-map type is {4}", features.MAP_TYPES == {4}, features.MAP_TYPES)
    check("a talk type is not also a kill type",
          not (features.TALK_TYPES & features.KILL_TYPES))


def test_dialog_ranking() -> None:
    from agent.dialog_policy import rank_dialog

    section("dialog ranking (question-aware, non-regressive)")

    def obs(message, replies, objective=None):
        knowledge = {"objective": {"text": objective}} if objective else {}
        return {
            "dialog": {"open": True, "text_ready": True, "ids_ready": True,
                       "message": message, "replies": replies},
            "knowledge": knowledge,
        }

    def rp(i, text):
        return {"index": i, "text": text, "reply_id": 100 + i}

    # (1) objective overlap decides, and the reason proves it is the PRIMARY path
    chosen = rank_dialog(obs("Que faisons-nous ?",
                              [rp(0, "aller tuer le Wabbit"), rp(1, "bonjour")],
                              objective="Wabbit"))
    check("objective overlap picks the matching reply",
          chosen.get("status") == "choose"
          and chosen["reply"]["index"] == 0
          and chosen["reason"] == "objective token overlap",
          chosen)

    # (2) objective overlap ties (no knowledge), the NPC question breaks it
    tied = rank_dialog(obs("combien de pommes ?",
                           [rp(0, "au revoir"), rp(1, "trois pommes")]))
    check("question text breaks an objective tie",
          tied.get("status") == "choose"
          and tied["reply"]["index"] == 1
          and tied["reason"] == "question tie-break",
          tied)

    # (3) nothing to go on (no overlap, no question match) => abstain, unchanged
    ambiguous = rank_dialog(obs("quel temps fait-il ?",
                                [rp(0, "alpha"), rp(1, "beta")]))
    check("no signal abstains instead of guessing",
          ambiguous.get("status") == "abstain", ambiguous)

    # (4) a dialog that is not ready waits rather than choosing
    not_ready = rank_dialog({"dialog": {"open": True, "text_ready": False,
                                        "replies": [rp(0, "x")]}})
    check("an unfinished dialog waits", not_ready.get("status") == "wait", not_ready)

    # (5) a closed / absent dialog returns None
    check("no open dialog returns None", rank_dialog({"dialog": {"open": False}}) is None)
    check("non-dict input returns None", rank_dialog(None) is None)


def test_prompt_fidelity() -> None:
    from agent.planner import _goal_mode
    from agent.autopilot import Autopilot

    section("prompt fidelity (criterion 1: a quests prompt never farms)")
    check("'faire des quetes' is a quest goal", _goal_mode("faire des quetes") == "quest")
    check("'farm kamas' is a farm goal", _goal_mode("farm kamas") == "farm")
    check("'auto' is a full-auto goal", _goal_mode("auto") == "auto")
    check("an empty prompt is Full Auto", _goal_mode("") == "auto")
    check("a bare order is not misread as a quest",
          _goal_mode("vendre 5 pommes") == "order", _goal_mode("vendre 5 pommes"))

    allowed = Autopilot._farm_allowed
    check("quests prompt + a kill objective => farming NOT allowed",
          allowed("quetes", {"current_objective": {"type_id": 0}}) is False)
    check("quests prompt + a delivery objective => farming allowed",
          allowed("quetes", {"current_objective": {"type_id": 2}}) is True)
    check("quests prompt + no objective => not allowed", allowed("quetes", None) is False)
    check("farm goal may always farm",
          allowed("farm", {"current_objective": {"type_id": 0}}) is True)
    check("full-auto may farm (no prompt to obey)",
          allowed("auto", {"current_objective": {}}) is True)


def test_named_mob_focus() -> None:
    from agent.planner import _farm_target, QuestPlanner

    section("named-mob focus (criterion 1: a named mob stays that mob)")
    check("a bare 'farm' names no mob", _farm_target("farm") is None, _farm_target("farm"))
    check("'farm wabbits' names the wabbits",
          bool(_farm_target("farm wabbits")) and "wabb" in _farm_target("farm wabbits"),
          _farm_target("farm wabbits"))

    visible = QuestPlanner._visible_target
    obs = {"map": {"groups": [{"monster_ids": [1001, 1002]}, {"monster_ids": [3000]}]}}
    check("the named target is visible on this map", visible(obs, 1001) is True)
    check("an absent target is reported not-visible (stay focused, do not grab others)",
          visible(obs, 7777) is False, visible(obs, 7777))
    check("no monster named => any group qualifies", visible(obs, None) is True)
    check("no map block is None (offline shape), not a guess",
          visible({}, 1001) is None, visible({}, 1001))


def test_thinking_layer() -> None:
    from types import SimpleNamespace
    from agent.thinking import ThinkingLayer, Thought

    section("thinking layer: never repeat the failed call, always terminate")

    def step(tool, args):
        return SimpleNamespace(tool=tool, args=args, cause="refused")

    # (A) after a failed quest.advance, the recovery proposal is a DIFFERENT call,
    # and the very next proposal is None — the layer cannot spin forever.
    layer = ThinkingLayer()
    failed = [step("quest.advance", {"quest_id": 5})]
    failed_sig = Thought("quest.advance", {"quest_id": 5}, "").signature()
    first = layer.propose("g", {}, failed)
    check("recovery proposes something when an alternative exists", first is not None)
    if first is not None:
        check("the proposal is not the call that just failed",
              first.signature() != failed_sig, first.signature())
    second = layer.propose("g", {}, failed)
    check("with no new alternative it returns None (no dead loop)", second is None,
          None if second is None else second.signature())

    # (B) a talk objective enumerates a bounded set of distinct untried proposals
    # and then stops. The point is termination + uniqueness, not the exact tool.
    obs = {"current_objective": {"type_id": 1,
                                 "params": json.dumps({"parameter0": 123})},
           "quests_active": {"55": {}}}
    layer2 = ThinkingLayer()
    seen, calls, terminated = set(), 0, False
    for _ in range(30):
        thought = layer2.propose("g2", obs, [])
        if thought is None:
            terminated = True
            break
        sig = thought.signature()
        check("proposal %d is unique" % calls, sig not in seen, sig)
        seen.add(sig)
        calls += 1
    check("the candidate enumeration terminates", terminated,
          "did not return None in 30 calls (%d seen)" % calls)
    check("it enumerated at least one proposal", calls >= 1, calls)


def test_go_relay_suites() -> None:
    section("Go relay tests (delegated to go, criterion 6)")
    if not shutil.which("go"):
        print("   SKIP: go not on PATH")
        return
    scripts = ROOT / "scripts"
    relay = scripts / "dofus-net-proxy.go"
    if not relay.exists():
        check("relay source present", False, str(relay))
        return
    test_files = sorted(p.name for p in scripts.glob("*_test.go"))
    if not test_files:
        check("at least one relay test present", False, "none in scripts/")
        return
    # Each scripts/*.go is its own package main; the relay test set must be built
    # by naming the relay file plus the test files together (not `go test ./...`,
    # which collides the many main()s).
    proc = subprocess.run(["go", "test", relay.name, *test_files],
                          cwd=str(scripts), capture_output=True, text=True, timeout=300)
    check("go relay tests pass (%s)" % ", ".join(test_files), proc.returncode == 0,
          (proc.stderr or proc.stdout).strip()[-300:])


def test_js_page_suites() -> None:
    section("JS page suites (delegated to node)")
    if not _have_node():
        print("   SKIP: node not on PATH")
        return
    for script in ("tools/test_keep_alive.js", "tools/test_npc_actor.js",
                   "tools/test_dialog_close.js", "tools/test_intervention_instrument.js"):
        path = ROOT / script
        if not path.exists():
            check("%s exists" % script, False, "missing")
            continue
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True)
        check("%s passes" % script, proc.returncode == 0,
              (proc.stderr or proc.stdout).strip()[-200:])


def test_bundle_resilience() -> None:
    section("Client bundle map-transition resilience")
    tool = ROOT / "tools" / "apply-map-resilience.js"
    bundle = ROOT / "desktop" / "client" / "www" / "build" / "script.js"
    if not _have_node():
        print("   SKIP: node not on PATH")
        return
    if not tool.exists():
        check("resilience tool present", False, str(tool))
        return
    if not bundle.exists():
        check("live client bundle present", False, str(bundle))
        return
    # --check writes nothing: it only classifies each rule as APPLIED /
    # APPLICABLE / STALE against the bundle the app actually serves.
    proc = subprocess.run(["node", str(tool), "--check"],
                          capture_output=True, text=True)
    check("every resilience rule resolves against the live bundle",
          proc.returncode == 0, (proc.stderr or proc.stdout).strip()[-250:])
    out = proc.stdout or ""
    # The bundle must ALREADY carry them: patch-engine.js installs these at build
    # time and patch-rules.txt carries them to the runtime patcher, so an
    # "APPLICABLE" here means a build lost a guard (KNOWN_GAPS G12).
    frac = re.search(r"applied (\d+)/(\d+)", out)
    applied, total = (frac.group(1), frac.group(2)) if frac else ("?", "?")
    check("live bundle carries every resilience rule (%s/%s)" % (applied, total),
          applied != "?" and applied == total,
          "run compile.bat - the rules are in patch-engine.js, not the bundle")

    # Second-owner guard: ClientBundle.cs can only install what the rules file
    # lists, so the same rules must exist there or an auto-update reverts them.
    rules_file = ROOT / "tools" / "patch-rules.json"
    try:
        payload = json.loads(rules_file.read_text(encoding="utf-8"))
        ids = {r["id"] for r in payload.get("rules", [])}
    except Exception as exc:  # noqa: BLE001
        check("patch-rules.json readable", False, str(exc))
        ids = set()
    missing = [i for i in range(20, 32) if i not in ids]
    check("resilience rules are in the runtime rule file (ids 20-31)",
          not missing, "missing %s - run python tools/make-patch-rules.py" % missing)
    dead = [i for i in (4, 5, 51) if i in ids]
    check("dead patch rules stay deleted (4/5/51)", not dead, "found %s" % dead)


def _have_node() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, text=True, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def test_intervention_instrument() -> None:
    section("Manual-intervention instrument (criterion 5: zero manual interventions)")
    import inspect
    from agent.autopilot import Autopilot

    class Stub:
        session = "deadbeef"

        def __init__(self):
            self._intervention_seen = 0
            self._intervention_total = 0
            self.rows = []

        def _append_log_line(self, blob):
            self.rows.append(blob)

    def obs(n):
        return {"user_input": {"during_run": n, "trusted": n,
                               "by_type": {"mousedown": n}, "last_ts": 1}}

    s = Stub()
    check("first human events are recorded",
          Autopilot._record_interventions(s, obs(3)) == 3 and s._intervention_total == 3,
          str(vars(s)))
    check("one row written per new intervention batch", len(s.rows) == 1, str(len(s.rows)))
    check("repeated counter is not re-counted",
          Autopilot._record_interventions(s, obs(3)) == 0 and s._intervention_total == 3)
    check("growth counts only the delta",
          Autopilot._record_interventions(s, obs(5)) == 2 and s._intervention_total == 5)
    # A page reload resets the client's counters. Without re-baselining, the first
    # step after a reload would re-report every pre-reload click as new.
    check("reload re-baselines instead of inflating",
          Autopilot._record_interventions(s, obs(1)) == 0 and s._intervention_total == 5
          and s._intervention_seen == 1, str(s._intervention_seen))
    check("no user_input field is not an error",
          Autopilot._record_interventions(s, {"in_world": True}) == 0)
    check("non-dict observation is not an error",
          Autopilot._record_interventions(s, None) == 0)
    check("summary reports manual_interventions",
          "manual_interventions" in inspect.getsource(Autopilot.summary))


def test_claim_quarantine() -> None:
    section("Claim quarantine (criterion 3: never re-send a packet that cost a connection)")
    import inspect
    from agent.planner import QuestPlanner

    key = (3841, 14070)
    p = QuestPlanner()
    p._pending_claim = (key, "quest.advance")
    p._claim_attempts[key] = 1
    note = ("a previous claim of objective %d of quest %d ended the connection; "
            "perform the objective instead" % (key[1], key[0]))
    # The objective is deliberately absent from the knowledge base here: the old
    # learning needed obj["type_id"], and an unknown tutorial objective resolves
    # to nothing, so it remembered nothing and re-sent the packet 287 times.
    check("a connection-ending claim quarantines the exact key",
          key not in p._claim_quarantine, "pre-existing quarantine?")
    p.note_result("quest.advance", False, note, {})
    check("  after the refusal the key is quarantined",
          key in p._claim_quarantine, str(p._claim_quarantine))
    p._claim_attempts.clear()
    p._claim_failures.clear()
    check("quarantine survives the counters being cleared",
          key in p._claim_quarantine, str(p._claim_quarantine))

    q = QuestPlanner()
    q._pending_claim = ((7, 8), "quest.advance")
    q.note_result("quest.advance", False, "objective not ready", {})
    check("an ordinary refusal does not quarantine",
          (7, 8) not in q._claim_quarantine, str(q._claim_quarantine))

    ok_p = QuestPlanner()
    ok_p._pending_claim = ((9, 10), "quest.advance")
    ok_p.note_result("quest.advance", True, "", {})
    check("a claim that worked does not quarantine",
          (9, 10) not in ok_p._claim_quarantine)

    src = inspect.getsource(QuestPlanner)
    check("quarantined claims route to the action path, not the quest",
          "key in self._claim_quarantine" in src
          and "attempts = max(attempts, 1)" in src,
          "must skip the packet, not abandon the objective")
    check("forked planners inherit the quarantine",
          "_claim_quarantine = set(self._claim_quarantine)" in src)


def test_failure_causes() -> None:
    section("Failure-cause taxonomy (criterion 4: the repair table needs real labels)")
    import inspect
    import json as _json
    from types import SimpleNamespace
    from agent.autopilot import Autopilot
    from agent.controller import CAUSES, RECOVERY, Controller, error_class
    from agent.runtime import ToolCall

    # Verbatim failure texts from the live baseline (docs/BASELINE.md), where all
    # seven produced `unknown`. The ids in them belong to one character's quest log:
    # what is under test is that the *phrasing* decides the cause, never the numbers.
    live = (
        ("a previous claim of objective 14070 of quest 3841 ended the connection; "
         "perform the objective instead", "quest.advance", "invalid_assumption"),
        ("nav.goto needs one of: cell, npc_id, direction, or a map_id different from 145490435",
         "nav.goto", "invalid_assumption"),
        ("the dialog offers no replies; it may need to be closed instead",
         "npc.talk", "invalid_assumption"),
        ("dialog close failed: TypeError: Cannot read properties of null (reading 'id')",
         "npc.talk", "stale_state"),
        ("client directive failed: no client instance is running (start one in DofusFarm first)",
         "quest.advance", "unavailable"),
        ("refused: fight blocked - no monster group on the current map",
         "fight.join", "wrong_target"),
        ("target monster 715354 is not on this map", "farm.start", "wrong_target"),
    )
    for text, tool, want in live:
        got = Controller.cause_of("failed", tool, False, text, {"map_id": 1}, {"map_id": 1})
        check("live failure classifies: %s" % tool, got == want, "%s != %s | %s"
              % (got, want, text[:58]))

    swapped = Controller.cause_of(
        "failed", "quest.advance", False,
        "a previous claim of objective 99 of quest 7 ended the connection; "
        "perform the objective instead", {}, {})
    check("the cause is phrasing, not quest or objective ids",
          swapped == "invalid_assumption", swapped)

    check("unknown stays reachable when nothing was measured",
          Controller.cause_of("failed", "x", False, None, None, None) == "unknown")
    # The hole that hid the second half of the baseline: a step with an observation
    # but no baseline used to be "unknown" while the after-state said plenty.
    check("a step with no baseline is judged from the observation it left",
          Controller.cause_of("noop", "quest.advance", True, None, None,
                              {"map_id": 1024, "in_dialog": True}) == "stale_state")
    check("every cause the classifier emits is declared",
          not ({Controller.cause_of("failed", "t", False, text, {}, {})
                for text, _, _ in live} - set(CAUSES)))
    check("an absent environment has no recovery call",
          "unavailable" in CAUSES and RECOVERY["unavailable"] is None)

    check("our own refusal prefix is a rule refusal",
          error_class("refused: fight blocked - no monster group on the current map")
          == "refused")
    check("a socket refusal is still transport",
          error_class("ConnectionRefusedError: connection refused") == "transport"
          and error_class("the target machine actively refused it") == "transport",
          "the prefix marker must not swallow the wire")

    def decide(error, tool="quest.advance", verdict="failed",
               before=None, after=None) -> Any:
        c = Controller(state_dir=Path(tempfile.gettempdir()), session="")
        return c.on_step(verdict, tool, False, error, before, after)

    d = decide(live[0][0])
    check("a rule refusal keeps the measured continue but carries the real cause",
          (d.action, d.cause) == ("continue", "invalid_assumption"),
          "%s/%s" % (d.action, d.cause))
    d = decide(live[1][0], tool="nav.goto")
    check("a call with no destination in it replans instead of re-observing",
          d.action == "replan", "%s/%s" % (d.action, d.cause))
    d = decide(live[4][0])
    check("an absent client replans as unavailable",
          (d.action, d.cause) == ("replan", "unavailable"),
          "%s/%s" % (d.action, d.cause))

    exploring = Controller(state_dir=Path(tempfile.gettempdir()), session="",
                           explore=1.0)
    de = exploring.on_step("failed", "nav.goto", False, live[1][0], {}, None)
    check("exploration never labels a recovery it cannot issue",
          not (de.action == "recover" and de.next_call is None),
          "%s/%s/%r" % (de.action, de.cause, de.next_call))

    class _Sink:
        _emit_decision = Autopilot._emit_decision

        def __init__(self) -> None:
            self.session = "gate"
            self.rows: List[Dict[str, Any]] = []

        def _append_log_line(self, blob: bytes) -> None:
            self.rows.append(_json.loads(blob.decode("utf-8")))

    sink = _Sink()
    call = ToolCall("quest.advance", {}, "goal")
    # The bridge reports success for a call the game refused: that is exactly the
    # case the row must not flatten into ok=True with a null error.
    sink._emit_decision(decide(live[0][0]), call, SimpleNamespace(ok=True, error=None),
                        {"map_id": 1}, ok=False, error=live[0][0])
    row = sink.rows[-1]
    check("the decision row records the outcome the controller was given",
          row["ok"] is False and "ended the connection" in str(row["error"]),
          "%r / %r" % (row["ok"], str(row["error"])[:50]))
    sink._emit_decision(decide(live[0][0]), call, SimpleNamespace(ok=True, error=None),
                        {"map_id": 1})
    check("with no outcome passed, the row still falls back to the result",
          sink.rows[-1]["ok"] is True)

    src = inspect.getsource(Autopilot)
    check("both decision sites pass the game-level outcome",
          "ok=step_ok, error=step_error" in src and "ok=False, error=refusal" in src)
    check("the goal the policy cannot express is logged as a decision",
          src.count("self._emit_decision(") == 3
          and src.count('"policy returned no call"') == 3,
          "one emit per decision path: %d" % src.count("self._emit_decision("))


def test_absent_client_recovery() -> None:
    section("Absent-client recovery (criterion 3: no window is a wait, not 27 failures)")
    import inspect
    import json as _json
    from agent.autopilot import ABSENT_CLIENT_MARKERS, Autopilot, client_absent
    from agent import autopilot as _autopilot_module

    src_of_recover = inspect.getsource(Autopilot._recover_world)

    check("the host's three 'no client' phrasings all read as absent",
          client_absent("no client instance is running (start one in DofusFarm first)")
          and client_absent("instance 'dofus-05' is not an active client window")
          and client_absent("instance 'x' is not an active client window "
                            "(no client window is open - launch an instance)"),
          str(ABSENT_CLIENT_MARKERS))
    check("an in-game refusal is not a missing client",
          not client_absent("refused: fight blocked - no monster group on the current map")
          and not client_absent(None) and not client_absent(""))

    class _Session:
        _recover_world = Autopilot._recover_world
        _wait_for_window = Autopilot._wait_for_window

        def __init__(self, comes_back: bool) -> None:
            self.session = "gate"
            self.dry_run = False
            self.rows: List[Dict[str, Any]] = []
            self._prepared_obs = {"map_id": 1}
            self._recoveries = 0
            self._recoveries_ok = 0
            self._recovery_wait_s = 0.0
            self._recent = ["x"]
            self._action_noops = {"a": 1}
            self._stall_since = 99
            self._index = 7
            self._farm_wait = True
            self._farm_wait_since = 1.0
            self._farm_wait_report = 1.0
            self._fight_wait_since = 1.0
            self._fight_wait_report = 1.0
            self._fight_arm_at = 1.0
            self._forced_call = object()
            self._comes_back = comes_back

        def _append_log_line(self, blob: bytes) -> None:
            self.rows.append(_json.loads(blob.decode("utf-8")))

        def _should_stop(self) -> Optional[str]:
            return None

        def _bridge_alive(self) -> bool:
            return True

        def _require_world(self, fresh: bool = False) -> bool:
            return self._comes_back

    import agent.bridge as _bridge
    original_count = _bridge.client_count
    _bridge.client_count = lambda *a, **k: 1
    try:
        back = _Session(True)
        ok = back._recover_world("no client instance is running", trigger="client_absent")
    finally:
        _bridge.client_count = original_count
    row = back.rows[-1]
    check("a rescued session reports the wait as a recovery row",
          ok and row["kind"] == "recovery" and row["trigger"] == "client_absent"
          and row["recovered"] is True and row["waited_s"] >= 0.0, str(row))
    check("the rescue is counted, and the stale per-step state is dropped",
          (back._recoveries, back._recoveries_ok) == (1, 1)
          and back._forced_call is None and back._action_noops == {}
          and back._farm_wait is False and back._stall_since == back._index,
          "%r %r" % (back._recoveries, back._forced_call))

    gone = _Session(False)
    _bridge.client_count = lambda *a, **k: 1
    try:
        failed = gone._recover_world("no client instance is running",
                                     trigger="client_absent")
    finally:
        _bridge.client_count = original_count
    frow = gone.rows[-1]
    check("a session that never comes back is reported as a failure, not a success",
          failed is False and frow["recovered"] is False
          and (gone._recoveries, gone._recoveries_ok) == (1, 0)
          and gone._recovery_wait_s == 0.0, "%r %r" % (frow, gone._recoveries_ok))

    # The presence gate on its own. This is what stops the wait from resolving on the
    # *carried* snapshot: with no page, that still claims the character is on a map,
    # so a world-only wait would exit instantly and re-dispatch into the same failure.
    wait = _Session(True)
    _bridge.client_count = lambda *a, **k: 2
    try:
        found = wait._wait_for_window()
    finally:
        _bridge.client_count = original_count
    check("an open window ends the presence wait", found is True)

    _bridge.client_count = lambda *a, **k: None
    wait._bridge_alive = lambda: False
    try:
        dead_host = wait._wait_for_window()
    finally:
        _bridge.client_count = original_count
    check("a host that stopped answering ends the run instead of parking it",
          dead_host is False)

    _bridge.client_count = lambda *a, **k: 0
    wait._should_stop = lambda: "stop file"
    try:
        stopped = wait._wait_for_window()
    finally:
        _bridge.client_count = original_count
    check("the stop file ends the presence wait", stopped is False)

    # Zero windows is not a window: the wait has to keep waiting. Without this the
    # loop could read "no client" as "client present", resolve on the carried
    # snapshot, and re-dispatch the directive that just failed -- an instant cycle.
    sleeps: List[float] = []
    real_sleep = _autopilot_module.time.sleep
    _autopilot_module.time.sleep = lambda seconds: sleeps.append(seconds)
    polls = []

    def _none_yet(*a, **k):
        polls.append(1)
        return 0 if len(polls) < 4 else None

    _bridge.client_count = _none_yet
    waiting = _Session(True)
    waiting._bridge_alive = lambda: True
    try:
        # count 0 three times, then the host stops answering -> the honest end.
        waiting._should_stop = lambda: None if len(polls) < 4 else "bridge gone"
        never = waiting._wait_for_window()
    finally:
        _bridge.client_count = original_count
        _autopilot_module.time.sleep = real_sleep
    check("an empty window count keeps the run waiting instead of resuming",
          never is False and len(polls) >= 3 and len(sleeps) >= 2,
          "%d polls, %s" % (len(polls), sleeps))

    check("the world wait is only asked after a window exists",
          "_wait_for_window() if trigger == \"client_absent\"" in src_of_recover)

    # The presence read itself. A failure must be `None` (unknown), never 0: the loop
    # treats 0 as "no window yet, keep waiting" and None as "ask whether the host is
    # even there", so a broken parse reported as 0 would park a live run forever.
    from agent import bridge as _bridge_mod

    def _probe(body: bytes, explode: bool = False) -> Any:
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return body

        class _Opener:
            def open(self, *a, **k):
                if explode:
                    raise OSError("no answer")
                return _Resp()

        saved = _bridge_mod.local_opener
        _bridge_mod.local_opener = lambda: _Opener()
        try:
            return _bridge_mod.client_count()
        finally:
            _bridge_mod.local_opener = saved

    check("the host's instance count is read from /agent/health",
          _probe(b'{"status":"ok","service":"dofus-agent-bridge","port":8893,'
                 b'"instances":3,"time":1}') == 3)
    check("a malformed health body is unknown, not zero windows",
          _probe(b'<html>proxy intercepted this</html>') is None
          and _probe(b'{"instances":null}') is None)
    check("a bridge that does not answer is unknown, not zero windows",
          _probe(b"", explode=True) is None)

    src = inspect.getsource(Autopilot)
    anchor = 'if not step_ok and client_absent(step_error):'
    settle = 'after = self._settle(before, after, call=call)'
    check("a dead window is waited on before any settle poll can run",
          anchor in src and settle in src and src.index(anchor) < src.index(settle)
          and "_recover_world(" in src[src.index(anchor):src.index(settle)]
          and 'trigger="client_absent"' in src[src.index(anchor):src.index(settle)],
          "the branch must be live, ordered first, and call the wait")
    check("the loop passes its own trigger, not the default",
          src.count('trigger="client_absent"') == 1
          and 'trigger == "client_absent"' in src,
          "%d call site(s)" % src.count('trigger="client_absent"'))
    check("a dead window ends the run with its own reason",
          'the client window is gone' in src
          and 'open an instance in DofusFarm' in src)
    check("the summary carries the recovery numbers",
          all(key in inspect.getsource(Autopilot.summary)
              for key in ("recoveries", "recoveries_ok", "recovery_wait_s")))


def test_repair_credit() -> None:
    section("Repair credit (criterion 4: a read cannot certify a recovery)")
    import inspect
    from agent.autopilot import Autopilot, PENDING_CREDIT_STEPS
    from agent.controller import Controller, Decision
    from agent.runtime import ToolCall

    def held() -> Any:
        loop = type("_Loop", (), {})()
        loop.controller = Controller(state_dir=Path(tempfile.gettempdir()), session="")
        loop._pending = Decision("recover", "incomplete", "held for test",
                                 {"tool": "state.snapshot", "args": {}}, "recover")
        loop._pending_age = 0
        loop._credit_pending = Autopilot._credit_pending.__get__(loop)
        return loop

    read = ToolCall("state.snapshot", {}, "goal")
    action = ToolCall("quest.advance", {"quest_id": 1}, "goal")

    loop = held()
    loop._credit_pending(read, "advanced")
    check("a successful read does not certify the repair it was following",
          not loop.controller.stats()["strategies"] and loop._pending is not None
          and loop._pending_age == 1,
          str(loop.controller.stats()["strategies"]))

    loop = held()
    for _ in range(PENDING_CREDIT_STEPS + 1):
        loop._credit_pending(read, "advanced")
    check("a repair no action answers is dropped, not guessed at",
          loop._pending is None and not loop.controller.stats()["strategies"],
          "%r %r" % (loop._pending, loop.controller.stats()["strategies"]))

    loop = held()
    loop._credit_pending(read, "advanced")
    loop._credit_pending(action, "advanced")
    stats = loop.controller.stats()["strategies"]
    check("the credit lands on the first step that could move the world",
          list(stats) == ["incomplete:recover"]
          and stats["incomplete:recover"]["n"] == 1
          and stats["incomplete:recover"]["ok"] == 1, str(stats))

    loop = held()
    loop._credit_pending(action, "failed")
    stats = loop.controller.stats()["strategies"]
    check("and a failing action is credited as a failure",
          stats["incomplete:recover"] == {"n": 1, "ok": 0, "score": 0.0}, str(stats))

    loop = held()
    loop._pending = None
    loop._credit_pending(action, "advanced")
    check("nothing is credited when no decision is held",
          not loop.controller.stats()["strategies"])

    src = inspect.getsource(Autopilot)
    check("the loop credits through the read-aware gate only",
          src.count("self._credit_pending(call, v)") == 1
          and "self.controller.observe_outcome(self._pending, verdict)"
          in inspect.getsource(Autopilot._credit_pending)
          and src.count("controller.observe_outcome") == 1,
          "%d direct credits" % src.count("controller.observe_outcome"))


def test_settle_waits_for_promised_effect() -> None:
    section("Settle window (criterion 2: an effect that lands late is not a no-op)")
    import inspect
    from agent import autopilot as ap
    from agent.autopilot import Autopilot, SETTLE_TOOLS
    from agent.runtime import ToolCall

    class _Clock:
        def __init__(self) -> None:
            self.t = 1000.0

        def monotonic(self) -> float:
            return self.t

        def sleep(self, seconds: float) -> None:
            self.t += seconds

        def time(self) -> float:
            return self.t

    class _Pilot:
        _settle = Autopilot._settle
        _world_key = Autopilot._world_key
        _action_complete = Autopilot._action_complete
        _record_settled = Autopilot._record_settled

        def __init__(self, states: List[Dict[str, Any]]) -> None:
            self.settle_s = 3.0
            self.think_s = 0.25
            self.states = list(states)
            self.polls = 0
            self.runtime = type("_R", (), {"observe": lambda *a, **k: None})()

        def observe(self, max_age_ms: Any = None, record: bool = True) -> Any:
            self.polls += 1
            return self.states.pop(0) if self.states else None

    def settle(tool: str, before: Dict[str, Any], after: Dict[str, Any],
               polls: List[Dict[str, Any]]) -> Any:
        pilot = _Pilot(polls)
        clock = _Clock()
        real = ap.time
        ap.time = clock
        try:
            best = pilot._settle(before, after, call=ToolCall(tool, {}, "goal"))
        finally:
            ap.time = real
        return pilot, best, clock.t - 1000.0

    base = {"cell": 1, "map_id": 7, "in_dialog": False}
    walked = {"cell": 2, "map_id": 7, "in_dialog": False}
    opened = {"cell": 2, "map_id": 7, "in_dialog": True}

    pilot, best, waited = settle("quest.advance", base, walked, [walked, walked, opened])
    check("a claim whose dialog opens one round trip later is judged on the dialog",
          bool((best or {}).get("in_dialog")) and pilot.polls >= 3,
          "%d polls -> %s" % (pilot.polls, best))

    pilot, best, waited = settle("inv.sort", {"cell": 1, "map_id": 7},
                                 {"cell": 9, "map_id": 7},
                                 [{"cell": 9, "map_id": 7}] * 8)
    check("a tool with no promise of its own is judged on the change it made, unpollled",
          pilot.polls == 0 and waited < 0.1 and (best or {}).get("cell") == 9,
          "%d polls, %.2f s" % (pilot.polls, waited))

    pilot, best, waited = settle("quest.advance", base, walked,
                                 [walked] * 40)
    check("the wait stays bounded by the tool's own window",
          waited <= 7.5 and pilot.polls < 40, "%.2f s after %d polls" % (waited, pilot.polls))

    src = inspect.getsource(Autopilot._settle)
    check("the quiet shortcut is the one reserved for promise-less tools",
          "not promised" in src and "promised = tool in SETTLE_TOOLS" in src)


def test_reads_are_not_actions() -> None:
    section("Idle waits (criterion 2: a read is a wait, not a step)")
    import inspect
    from agent.autopilot import (Autopilot, ESCALATE_TRIES, MAX_CONSECUTIVE_WAITS,
                                 ToolCall, WAIT_POLL_S, fell_out_of_world)
    from agent import autopilot as _am

    check("a session that stayed in the world is not a fall-out",
          fell_out_of_world({"in_world": True}, {"in_world": True}) is None
          and fell_out_of_world(None, {"in_world": False}) is None
          and fell_out_of_world({"in_world": True}, {"map_id": 10340}) is None)
    check("a world that vanished without saying why is still a fall-out",
          fell_out_of_world({"in_world": True}, {"in_world": False}) == "")
    check("the client's own reason travels to the caller",
          fell_out_of_world({"in_world": True},
                            {"in_world": False,
                             "last_disconnect": {"reason": "INACTIVITY"}}) == "INACTIVITY")
    check("a map transition that briefly reports no map does not end the run",
          fell_out_of_world({"in_world": False}, {"in_world": False}) is None)
    check("a malformed disconnect field cannot crash the test",
          fell_out_of_world({"in_world": True},
                            {"in_world": False, "last_disconnect": "INACTIVITY"}) == "")

    # Drive the real loop with a policy that only ever wants a look. This is the
    # baseline's dominant shape: 3,010 of its 4,287 steps were `state.snapshot`,
    # 3,005 of them scored `advanced`, and two runs of them held 57% of the total.
    class _Policy:
        def __init__(self, tool: str = "state.snapshot") -> None:
            self.tool = tool
            self.calls = 0

        def choose(self, goal, obs, tools):     # noqa: ARG002 - shape, not behaviour
            self.calls += 1
            return ToolCall(self.tool, {}, goal)

    class _Result:
        def __init__(self, data: Dict[str, Any]) -> None:
            self.ok = True
            self.error = None
            self.data = data

    class _Executor:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.obs = obs
            self.calls: List[str] = []

        def execute(self, tool, args, **kw):      # noqa: ARG002 - the shape is enough
            self.calls.append(tool)
            return _Result({"response": {"result": {"ok": True}},
                            "snapshot": dict(self.obs)})

    class _Runtime:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.policy = _Policy()
            self.instance = "dofus-00"
            self.obs = obs
            self.executor = _Executor(obs)

        def current_observation(self):
            return dict(self.obs), 5.0, "cache"

        def observe(self, obs, source="live"):    # noqa: ARG002 - no trajectory writer
            return None

        def note_observation(self, obs):
            return None

    dirs: List[Path] = []

    def drive(directives: int, obs: Optional[Dict[str, Any]] = None,
              tool: str = "state.snapshot", escalate=None):
        tmp = Path(tempfile.mkdtemp())
        dirs.append(tmp)
        rt = _Runtime(dict(obs if obs is not None
                           else {"in_world": True, "map_id": 10340, "cell": 300}))
        rt.policy.tool = tool
        ap = Autopilot(runtime=rt,
                       goals=["work the %d goal" % i for i in range(directives)],
                       log_path=tmp / "autopilot.jsonl", stop_file=tmp / "STOP",
                       max_steps=directives, dry_run=False, preflight=False,
                       minutes=0, interval=0.0, steps_per_goal=1)
        # The gate must not write a strategy table into the live state directory.
        ap.controller.state_dir = tmp
        if escalate is not None:
            ap.thinking.propose = escalate
        labels: List[Any] = []
        real_label = ap.verifier.label
        ap.verifier.label = lambda **kw: (
            labels.append(kw.get("tool")), real_label(**kw))[1]
        consulted: List[Any] = []
        real_on_step = ap.controller.on_step
        ap.controller.on_step = lambda *a, **k: (
            consulted.append(1), real_on_step(*a, **k))[1]
        # A live-shaped run, minus the seconds: the loop's own pacing is asserted
        # separately, and 120 wait passes must not cost the gate 40 seconds.
        real_sleep = _am.time.sleep
        _am.time.sleep = lambda seconds: None
        try:
            summary = ap._run()
        finally:
            _am.time.sleep = real_sleep
        rows = [json.loads(line) for line in
                (tmp / "autopilot.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        return ap, summary, rows, labels, consulted, rt

    try:
        ap, summary, rows, labels, consulted, rt = drive(2)
        waits = [r for r in rows if r.get("kind") == "wait"]
        # A step row is the one row type with no `kind` of its own; decision rows
        # carry the tool too and must not be read as steps.
        step_rows = [r for r in rows if "kind" not in r]
        check("two directives cost two step rows and nothing else",
              summary["steps"] == 2 and len(step_rows) == 2,
              "%r step rows %d" % (summary["steps"], len(step_rows)))
        check("the looking between them is a wait, not a step",
              summary["waits"] == 2 * MAX_CONSECUTIVE_WAITS
              and len(waits) == 2 * MAX_CONSECUTIVE_WAITS,
              "waits %r / rows %d" % (summary.get("waits"), len(waits)))
        check("a wait produces no verdict and no reward",
              len(labels) == 2 and len(consulted) == 2,
              "%d labels, %d controller calls over %d passes"
              % (len(labels), len(consulted), rt.policy.calls))
        check("the spin is bounded: past the limit the read is dispatched again",
              all(s.get("tool") == "state.snapshot" for s in step_rows)
              and rt.executor.calls == ["state.snapshot"] * 2
              and rt.policy.calls == 2 * (MAX_CONSECUTIVE_WAITS + 1),
              "%d passes, %d dispatches" % (rt.policy.calls, len(rt.executor.calls)))
        check("a wait row carries the goal, the session and a count",
              (waits[0] if waits else {}).get("goal") == "work the 0 goal"
              and (waits[0] if waits else {}).get("session") == ap.session
              and (waits[0] if waits else {}).get("consecutive") == 1
              and "at" in (waits[0] if waits else {}), str(waits[:1]))
        check("an idle wait still says so in its own words",
              (waits[0] if waits else {}).get("reason") == "the policy had no action to take",
              str(waits[:1]))
        check("a step ends the streak and drops a reason that no longer applies",
              ap._consecutive_waits == 0 and ap._forced_reason == "" and ap._waits > 0,
              "%d %r" % (ap._consecutive_waits, ap._forced_reason))
        from agent.autopilot import Step
        ap._consecutive_waits = MAX_CONSECUTIVE_WAITS - 1
        ap._forced_reason = "a reason written for a call already made"
        ap._emit(Step(index=0, goal="g", tool="inv.sort", args={}, ok=True,
                      error=None, latency_ms=1.0, verdict="advanced"))
        check("the reset is in the step recorder itself, not only in the loop",
              ap._consecutive_waits == 0 and ap._forced_reason == "",
              "%d %r" % (ap._consecutive_waits, ap._forced_reason))

        # The reason is the thinking layer's own sentence, when it has one.
        ap._forced_reason = "wait for the NPC to finish talking"
        held = ap._hold_wait("do all the quests", {"in_dialog": True, "in_world": True})
        last = json.loads((dirs[-1] / "autopilot.jsonl").read_text(
            encoding="utf-8").splitlines()[-1])
        check("a directed wait is recorded with the reason that directed it",
              held is True and last["kind"] == "wait"
              and last["reason"] == "wait for the NPC to finish talking"
              and last["in_dialog"] is True, str(last))
        check("and the reason is spent, not stamped on every later wait",
              ap._forced_reason == "", repr(ap._forced_reason))

        # Pacing: the same cadence the loop had when the wait was a directive.
        sleeps: List[float] = []
        real_sleep = _am.time.sleep
        _am.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            ap.dry_run = False
            ap.interval = 0.0
            ap._hold_wait("do all the quests", {"in_world": True})
            paced = list(sleeps)
            sleeps.clear()
            ap.interval = 2.0
            ap._hold_wait("do all the quests", {"in_world": True})
            paced_by_interval = list(sleeps)
            sleeps.clear()
            ap.dry_run = True
            ap.interval = 0.0
            ap._hold_wait("do all the quests", {"in_world": True})
            dry = list(sleeps)
        finally:
            ap.dry_run = False
            _am.time.sleep = real_sleep
        check("a wait holds the loop's measured cadence, no faster",
              paced == [WAIT_POLL_S], str(paced))
        check("a slower --interval still wins", paced_by_interval == [2.0],
              str(paced_by_interval))
        check("a dry run has no world to wait for and does not sleep", dry == [], str(dry))

        # A kick that lands during a wait must be a recovery, not more waiting: no
        # step is dispatched here, so the post-step check would never have run.
        recovered: List[Any] = []
        real_recover = ap._recover_world

        def _spy(reason: Optional[str] = None, trigger: str = "fell_out") -> bool:
            recovered.append((reason, trigger))
            return not fail_next

        fail_next = False
        ap._recover_world = _spy
        try:
            rt.obs = {"in_world": False, "last_disconnect": {"reason": "INACTIVITY"}}
            out = ap._hold_wait("do all the quests", {"in_world": True, "map_id": 1})
            rescued = list(recovered)
            # The count measured a wait inside a session that has ended, so a
            # rescued world must start with a full budget of its own.
            ap._consecutive_waits = MAX_CONSECUTIVE_WAITS
            rt.obs = {"in_world": False, "last_disconnect": {"reason": "KICKED"}}
            ap._hold_wait("do all the quests", {"in_world": True, "map_id": 1})
            streak = ap._consecutive_waits
            fail_next = True
            rt.obs = {"in_world": False}
            given_up = ap._hold_wait("do all the quests", {"in_world": True, "map_id": 1})
        finally:
            ap._recover_world = real_recover
        check("a fall-out noticed during a wait hands over to the recovery path",
              out is True and rescued == [("INACTIVITY", "wait_fell_out")],
              "%r %r" % (out, rescued))
        check("and the wait budget restarts against the rescued world",
              streak == 0, "streak %d" % streak)
        check("a world that does not come back ends the run, it does not keep waiting",
              given_up is False)
        check("a silent fall-out still reaches the recovery path",
              (recovered[-1] if recovered else None) == (None, "wait_fell_out"),
              str(recovered[-1:]))

        # A wait that reaches the bound is evidence the goal has nothing left, so the
        # loop asks the thinking layer for an untried interaction. Two failure modes
        # matter: taking a read as the answer (which resets the streak and doubles the
        # spin -- the first version of this did exactly that), and escalating forever.
        from agent.thinking import Thought
        seen: List[int] = []

        def _only_reads(goal, observation, steps, decision):
            seen.append(1)
            return Thought("state.snapshot", {}, "refresh the observation")

        _, summary2, rows2, _, _, rt2 = drive(1, escalate=_only_reads)
        check("a read is never accepted as an escalation",
              not any(r.get("escalated") for r in rows2 if r.get("kind") == "wait")
              and summary2["waits"] == MAX_CONSECUTIVE_WAITS,
              "waits %r asks %d" % (summary2.get("waits"), len(seen)))
        check("and the search is bounded, not re-asked every pass",
              len(seen) == ESCALATE_TRIES and rt2.executor.calls == ["state.snapshot"],
              "%d asks, dispatched %r" % (len(seen), rt2.executor.calls))

        asks: List[int] = []

        def _escalate(goal, observation, steps, decision):
            asks.append(1)
            # First the read, then a real action -- the filter must skip the read.
            return Thought("farm.stop", {}, "stop the standing order") \
                if len(asks) > 1 else Thought("state.snapshot", {}, "refresh")

        ap3, summary3, rows3, _, _, rt3 = drive(2, escalate=_escalate)
        marked = [r for r in rows3 if r.get("kind") == "wait" and r.get("escalated")]
        check("an untried action is taken at the bound and named in the row",
              len(marked) == 2 and all(m["escalated"] == "farm.stop" for m in marked)
              and all(m["consecutive"] == MAX_CONSECUTIVE_WAITS for m in marked),
              str(marked))
        check("the escalated call is dispatched on the next pass",
              rt3.executor.calls == ["farm.stop", "farm.stop"], str(rt3.executor.calls))
        check("one escalation cannot chain into the next: the budget restarts",
              summary3["waits"] == 2 * MAX_CONSECUTIVE_WAITS
              and ap3._consecutive_waits == 0 and len(asks) <= 2 * ESCALATE_TRIES,
              "waits %r asks %d" % (summary3.get("waits"), len(asks)))
        check("and each goal escalates for itself: a repair queued by one is not "
              "dispatched under the next",
              all(m["goal"] == "work the %d goal" % i for i, m in enumerate(marked))
              and [s.get("goal") for s in rows3 if "kind" not in s]
              == ["work the 0 goal", "work the 1 goal"],
              str([(s.get("goal"), s.get("tool")) for s in rows3 if "kind" not in s]))

        # An escalation is a proposal, not a pass: it still goes through the guard, and
        # a call the schema does not know costs a refusal row -- it must not become a
        # new kind of spin.
        def _bogus(goal, observation, steps, decision):
            return Thought("inv.sort", {"sort": "bag"}, "the layer is guessing")

        _, summary4, rows4, _, _, rt4 = drive(1, escalate=_bogus)
        blocked = [r for r in rows4 if "kind" not in r and r.get("verdict") == "blocked"]
        check("an escalated call the schema rejects is refused, not dispatched",
              rt4.executor.calls == [] and len(blocked) == 1
              and blocked[0]["tool"] == "inv.sort"
              and "schema rejected" in str(blocked[0].get("error")),
              "%r %r" % (rt4.executor.calls, [b.get("error") for b in blocked][:1]))
        check("and the refusal ends the goal rather than looping the wait",
              summary4["steps"] == 1 and summary4["waits"] == MAX_CONSECUTIVE_WAITS,
              "%r %r" % (summary4.get("steps"), summary4.get("waits")))

        src_run = inspect.getsource(Autopilot._run)
        src_wait = inspect.getsource(Autopilot._hold_wait)
        check("the wait is taken before the guard, so a read cannot be refused or streaked",
              src_run.index("self._hold_wait(goal, before)")
              < src_run.index("refusal = self.guard(call)"))
        check("the bound decides in the loop; the wait itself cannot retire a goal",
              "self._consecutive_waits < MAX_CONSECUTIVE_WAITS" in src_run
              and "ended_because" not in src_wait
              and "_retire_named_quest" not in src_wait)
        check("a wait has no way to produce a step row, a verdict, or a reward",
              "Step(" not in src_wait and "self._emit(" not in src_wait
              and "on_step" not in src_wait)
        check("the limit is above every legitimate wait in the baseline",
              MAX_CONSECUTIVE_WAITS > 6 and WAIT_POLL_S > 0.05,
              "limit %d, poll %.2f" % (MAX_CONSECUTIVE_WAITS, WAIT_POLL_S))
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)


def test_dialog_labels() -> None:
    section("Dialog labels (criterion 4: a reply needs its outcome recorded to be learned)")
    import inspect
    import json as _json
    from agent.autopilot import Autopilot

    class _Policy:
        last_dialog_decision = {"reason": "question tie-break", "quest_id": 3841,
                                "scores": [{"index": 0, "text": "Yes.", "score": 0.0,
                                            "question": 0.5},
                                           {"index": 1, "text": "No.", "score": 0.0,
                                            "question": 0.0}]}

    class _Runtime:
        policy = _Policy()

    class _Session:
        _record_dialog = Autopilot._record_dialog

        def __init__(self, after: Dict[str, Any]) -> None:
            self.session = "gate"
            self._index = 4
            self._dialogs = 0
            self._dialog_stalls = 0
            self.rows: List[Dict[str, Any]] = []
            self.runtime = _Runtime()

        def _append_log_line(self, blob: bytes) -> None:
            self.rows.append(_json.loads(blob.decode("utf-8")))

    PAGE = "Hello, Aiai. I'm a travelling sorcerer."
    REPLIES = [{"index": 0, "text": "Say you want to change class."},
               {"index": 1, "text": "Never mind."}]
    before = {"dialog": {"open": True, "npc_id": 771, "name_id": "Sorcerer",
                         "message": PAGE, "replies": REPLIES},
              "knowledge": {"objective_id": 14070},
              # A free-form objective is the one a reply can never validate; the row has
              # to carry the type or the census cannot tell the two stalls apart.
              "current_objective": {"objective_id": 14070, "type_id": 12}}
    call = type("C", (), {"args": {"quest_id": 3841, "reply": 0,
                                   "reply_text": "Say you want to change class."}})()

    class _Result:
        def __init__(self, ok=True, data=None, error=None):
            self.ok, self.data, self.error = ok, data, error

    SENT = _Result(True, {"replied": 0, "replies": 2, "reply_id": 41457,
                          "reply_text": "Say you want to change class.",
                          "via": "gui.npcDialogHandler._uiReplyHandler"})

    def record(after, args=call.args, state=before, result=None):
        s = _Session(after)
        c = type("C", (), {"args": dict(args)})()
        s._record_dialog(c, state, after, result)
        return s, (s.rows[-1] if s.rows else None)

    moved = {"dialog": {"open": True, "npc_id": 771, "message": "Which class interests you?"}}
    s, row = record(moved, result=SENT)
    check("a reply that moved the conversation is recorded as advanced, with its features",
          row is not None and row["kind"] == "dialog" and row["outcome"] == "advanced"
          and row["asked"] == 0 and row["npc_id"] == 771 and row["quest_id"] == 3841
          and row["objective_id"] == 14070 and row["reason"] == "question tie-break"
          and len(row["scores"]) == 2 and row["page"] == PAGE
          and row["replies"] == [r["text"] for r in REPLIES], str(row))
    check("the score that decided it is stored, so a scorer can be trained on the pair",
          row["scores"][0]["score"] == 0.0 and row["scores"][0]["question"] == 0.5,
          str(row["scores"]))
    check("and it is counted, not just written",
          (s._dialogs, s._dialog_stalls) == (1, 0), str(s._dialogs))
    # G21: the dispatch half of the pair. The baseline replays one page 87 times against
    # reply id 41457, and the wire log shows the server answering it with a leave. Without
    # these fields the row cannot say who ended the conversation.
    check("the id and the handler that answered are recorded with the choice",
          row["reply_id"] == 41457
          and row["via"] == "gui.npcDialogHandler._uiReplyHandler"
          and row["dispatch_ok"] is True and row["error"] is None, str(row))
    check("the objective type travels with the row, so a free-form stall is not read "
          "as a bad ranking", row["objective_type"] == 12, str(row.get("objective_type")))
    check("the page that came back is recorded, not just its verdict",
          row["page_after"] == "Which class interests you?", str(row.get("page_after")))
    _, row = record(moved)
    check("a row is still written when the client gives no dispatch data back",
          row is not None and row["reply_id"] is None and row["via"] is None
          and row["dispatch_ok"] is True, str(row))
    _, row = record(moved, result=_Result(False, None, "dialog has 2 replies; asked for index 5"))
    check("a refused dispatch is a failed dispatch, and its reason is kept",
          row["dispatch_ok"] is False and row["error"].startswith("dialog has 2 replies"),
          str(row))

    _, row = record({"dialog": {"open": True, "npc_id": 771, "message": PAGE}})
    check("the same page after a reply is a stall, not silence",
          row["outcome"] == "same page" and row["asked"] == 0, str(row))
    s2, row = record({"dialog": {"open": False}})
    check("a reply that closed the dialog is recorded as closed",
          row["outcome"] == "closed" and s2._dialogs == 1, str(row))
    check("and says who closed it: we did not ask, so the game ended it",
          row["ended_by"] == "after_reply" and row["page_after"] is None, str(row))
    _, row = record({"map_id": 10340})
    check("an after-state without a dialog block says nothing and is called unknown",
          row["outcome"] == "unknown" and row["ended_by"] is None, str(row))

    # Non-dialog steps must not write rows: this instrument is a pairing, not a trace.
    for args in ({"quest_id": 3841}, {"quest_id": 3841, "npc_id": 5}, {}):
        _, row = record(moved, args=args)
        check("a %s step writes no dialog row" % (args or "bare"), row is None, str(row))
    _, closed = record(moved, args={"quest_id": 3841, "close": True})
    check("an explicit close is a dialog action and is recorded as asked=close",
          closed is not None and closed["asked"] == "close"
          and closed["outcome"] == "advanced", str(closed))
    _, closed = record({"dialog": {"open": False}}, args={"quest_id": 3841, "close": True})
    check("a close we asked for is credited to us, not to the server",
          closed["outcome"] == "closed" and closed["ended_by"] == "asked", str(closed))

    s4 = _Session(moved)
    s4._record_dialog(type("C", (), {"args": {"reply": 1, "reply_text": "No."}})(),
                      {"dialog": {"open": False, "message": PAGE}}, moved)
    check("a step taken with no dialog open cannot be attributed to one",
          s4.rows == [] and s4._dialogs == 0, str(s4.rows))

    src = inspect.getsource(Autopilot._run)
    check("the pairing is taken on the step path, where both states and the dispatch "
          "result exist",
          src.index("self._record_dialog(call, before, after, result)") > src.index(
              "fight_estimate, fight_outcome = self._track_fight(before, after)"))
    summary_src = inspect.getsource(Autopilot.summary)
    check("the summary reports dialog attempts and stalls",
          '"dialogs": self._dialogs' in summary_src
          and '"dialog_stalls": self._dialog_stalls' in summary_src)


def test_report_instrument() -> None:
    section("Baseline report (criterion 5: the instrument that measures the run)")
    import contextlib
    import importlib.util
    import io

    script = REPO_ROOT / "tools" / "recovery_report.py"
    check("the report tool exists", script.exists(), str(script))
    spec = importlib.util.spec_from_file_location("recovery_report", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run_report(lines: List[str]) -> str:
        tmp = Path(tempfile.mkdtemp())
        log = tmp / "log.jsonl"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = module.main([str(log)])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return "%s\n(exit %s)" % (buf.getvalue(), code)

    rows = [
        json.dumps({"kind": "recovery", "session": "s", "trigger": "client_absent",
                    "reason": "no client instance", "recovered": True,
                    "waited_s": 3.5, "at": 1000.0}),
        json.dumps({"kind": "wait", "session": "s", "goal": "g", "consecutive": 1,
                    "reason": "the policy had no action to take", "escalated": "chat.say",
                    "at": 1001.0}),
        json.dumps({"kind": "wait", "session": "s", "goal": "g", "consecutive": 2,
                    "reason": "the policy had no action to take", "at": 1002.0}),
        json.dumps({"kind": "dialog", "session": "s", "index": 0, "quest_id": 1,
                    "objective_id": 2, "npc_id": 7, "name_id": "Sorcerer",
                    "page": "Hello", "replies": ["a", "b"], "asked": 0,
                    "reply_text": "a", "reason": "question tie-break",
                    "scores": [{"index": 0, "score": 0.0, "question": 0.5}],
                    "outcome": "same page", "at": 1003.0}),
        # The same page, asked again, this time with the G21 dispatch fields: a row written
        # before they existed must still parse, and a repeat must be called out.
        json.dumps({"kind": "dialog", "session": "s", "index": 1, "quest_id": 1,
                    "objective_id": 2, "objective_type": 12, "npc_id": 7,
                    "name_id": "Sorcerer", "page": "Hello", "replies": ["a", "b"],
                    "asked": 0, "reply_text": "a", "reply_id": 41457,
                    "via": "gui.npcDialogHandler._uiReplyHandler",
                    "dispatch_ok": False, "error": "asked for index 5",
                    "reason": "only reply", "scores": [],
                    "outcome": "same page", "ended_by": None,
                    "page_after": "Hello", "at": 1003.5}),
        json.dumps({"kind": "dialog", "session": "s", "index": 2, "quest_id": 1,
                    "objective_id": 2, "objective_type": 12, "npc_id": 7,
                    "name_id": "Sorcerer", "page": "Hello", "replies": ["a"],
                    "asked": 0, "reply_text": "a", "reply_id": 41457, "dispatch_ok": True,
                    "reason": "only reply", "scores": [], "outcome": "closed",
                    "ended_by": "after_reply", "page_after": None, "at": 1003.8}),
        json.dumps({"index": 0, "session": "s", "goal": "g", "tool": "quest.advance",
                    "args": {}, "ok": True, "error": None, "latency_ms": 20.0,
                    "verdict": "advanced", "at": 1004.0}),
        json.dumps({"kind": "decision", "session": "s", "action": "recover",
                    "cause": "incomplete", "tool": "quest.advance", "at": 1005.0}),
    ]
    text = run_report(rows)
    check("the report reads every row kind the loop writes",
          "idle waits (G18" in text and "dialog replies" in text
          and "recovery waits" in text and "productive-action success" in text,
          text[:120])
    check("waits are totalled with the escalation shown",
          "2 passes in 1 run(s)" in text and "escalated to a real action" in text,
          "\n".join(l for l in text.splitlines() if "passes" in l or "escalated" in l))
    check("a stall is reported as a stall, and the dead primary signal is counted",
          "2  same page" in text and "objective overlap > 0: 0 of 3" in text,
          "\n".join(l for l in text.splitlines() if "same page" in l or "overlap" in l))
    # G21: the report has to answer *who ended the dialog* and *how many times the same
    # page was re-asked*, or the census reads as a ranking failure when it is a loop.
    check("a row written before the dispatch fields still parses and is counted",
          "dialog replies" in text and "3 attempt(s)" in text, text[:400])
    check("the census says the game ended that dialog, not us",
          "dialog ended by: {'after_reply': 1}" in text,
          "\n".join(l for l in text.splitlines() if "ended by" in l))
    check("the objective type is shown, so a free-form stall is visible as one",
          "objective type ids: {'12': 2}" in text,
          "\n".join(l for l in text.splitlines() if "objective type" in l))
    check("a refused dispatch is not silently counted as an attempt that sent",
          "dispatch refused (nothing went on the wire): 1" in text,
          "\n".join(l for l in text.splitlines() if "refused" in l))
    check("re-asking one page is called out by name",
          "NOTE re-asked 3 x" in text,
          "\n".join(l for l in text.splitlines() if "re-asked" in l))
    check("the productive table counts the one world-changing step",
          "quest.advance" in text and "1       1" in text,
          "\n".join(l for l in text.splitlines() if "quest.advance" in l))

    old = run_report([json.dumps({"index": 0, "session": "s", "goal": "g",
                                 "tool": "nav.goto", "args": {}, "ok": True,
                                 "error": None, "latency_ms": 5.0,
                                 "verdict": "noop", "at": 1000.0})])
    check("a log from before the instruments says so instead of printing zero",
          "predates the recovery instrument" in old
          and "predates the wait instrument" in old
          and "predates the dialog instrument" in old, old[:200])


def test_dialog_repeat_guard() -> None:
    section("Dialog repeats (criterion 3: an answer that changed nothing is not a plan)")
    import inspect
    from agent.planner import DIALOG_ASK_LIMIT, QuestPlanner

    HELMET = "Don't tell me you need help putting on a helmet?"

    def obs(message=HELMET, objective_id=14070):
        return {
            "dialog": {"open": True, "npc_id": 3888, "name_id": "Ayin Spyr",
                       "text_ready": True, "ids_ready": True, "message": message,
                       "reply_count": 1, "ids_present": True,
                       "replies": [{"index": 0, "text": "Yes, please.",
                                    "reply_id": 41457}]},
            "knowledge": {"quest_id": 3841, "objective_id": objective_id},
            "current_objective": {"objective_id": objective_id, "type_id": 12},
        }

    def asked(call) -> bool:
        return bool(call is not None and call.args.get("reply") is not None)

    p = QuestPlanner()
    first = p._dialog_call(obs(), "quest 3841")
    check("a page the objective has never been asked about is answered",
          asked(first) and first.args["reply"] == 0, first)
    check("one retry is allowed, because the page can lag the world",
          asked(p._dialog_call(obs(), "quest 3841")), "second ask refused")
    third = p._dialog_call(obs(), "quest 3841")
    check("the ask after that closes the conversation instead of repeating it",
          third is not None and third.args.get("close") is True and not asked(third),
          third)
    fourth = p._dialog_call(obs(), "quest 3841")
    check("a spent page is refused outright, so the re-open loop cannot resume",
          fourth is None, fourth)
    check("the refusal carries its own status, which is what the step note prints",
          isinstance(p.last_dialog_decision, dict)
          and p.last_dialog_decision.get("status") == "exhausted", p.last_dialog_decision)
    check("the memory is keyed by the limit, not by a magic number in the test",
          DIALOG_ASK_LIMIT == 2, str(DIALOG_ASK_LIMIT))

    # The three ways a refusal would be wrong: a different objective, a page that
    # really did move, and a fork that forgot.
    q = QuestPlanner()
    for _ in range(DIALOG_ASK_LIMIT + 2):
        q._dialog_call(obs(), "quest 3841")
    check("a reply spent on one objective is not spent on the next",
          asked(q._dialog_call(obs(objective_id=14071), "quest 3841")), "objective-blind")
    check("a page that changed is a fresh question",
          asked(q._dialog_call(obs("Which class interests you?"), "quest 3841")),
          "page-blind")
    twin = q.fork()
    spent = twin._dialog_call(obs(objective_id=14070), "quest 3841")
    check("a fork inherits which pages are spent instead of starting the loop again",
          spent is None, spent)
    src = inspect.getsource(QuestPlanner.fork)
    check("and the inheritance is real code, not a side effect of a shared default",
          "_dialog_exhausted = set(self._dialog_exhausted)" in src, src[:120])


def run() -> Tuple[int, List[str]]:
    test_verdict_vocabulary()
    test_verifier_rules()
    test_verifier_model_contract()
    test_tool_vocabulary()
    test_forbidden_and_validation()
    test_objective_buckets()
    test_dialog_ranking()
    test_prompt_fidelity()
    test_named_mob_focus()
    test_thinking_layer()
    test_claim_quarantine()
    test_failure_causes()
    test_absent_client_recovery()
    test_repair_credit()
    test_reads_are_not_actions()
    test_dialog_labels()
    test_dialog_repeat_guard()
    test_report_instrument()
    test_settle_waits_for_promised_effect()
    test_intervention_instrument()
    test_js_page_suites()
    test_bundle_resilience()
    test_go_relay_suites()
    return _CHECKS, _FAILURES


def main(argv: Optional[List[str]] = None) -> int:
    checks, failures = run()
    print("\n%d checks, %d failed" % (checks, len(failures)))
    if failures:
        print("\nFAILURES:\n  " + "\n  ".join(failures))
        return 1
    print("all regression cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
