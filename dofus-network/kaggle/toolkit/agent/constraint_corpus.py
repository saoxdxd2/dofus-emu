"""Deterministic constraint corpus for the tool vocabulary.

This is a test corpus, not training data. Each row is a legal tool call paired with
a real handler/guard refusal and the controller outcome it must produce. It makes
the failure taxonomy exhaustive without pretending that rule-derived labels are
game outcomes.

    python -m agent.constraint_corpus --check
    python -m agent.constraint_corpus --out data/constraint_cases.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.controller import CAUSES, Controller
from agent.schema import TOOLS, tool_names
from agent.verifier import Verdict

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "constraint_cases.jsonl"

# expected_actions is the controller's intended routing, not a strict single
# choice: a wrong target may retry once or recover, depending on history.
_WRONG = {"retry", "recover", "replan"}
_BAD_ASSUMPTION = {"replan", "continue"}
_UNAVAILABLE = {"replan"}
_SUCCESS = {"continue"}


def _case(tool: str, error: Optional[str], cause: str,
          actions: Sequence[str], *,
          verdict: str = "failed", ok: bool = False,
          before: Optional[Dict[str, Any]] = None,
          after: Optional[Dict[str, Any]] = None,
          detail: Any = None, repeat: int = 1,
          note: str = "", family: str = "") -> Dict[str, Any]:
    row = {
        "tool": tool,
        "error": error,
        "verdict": verdict,
        "ok": ok,
        "before": before if before is not None else {"map_id": 1, "cell": 1},
        "after": after if after is not None else {"map_id": 1, "cell": 1},
        "detail": detail,
        "expected_cause": cause,
        "expected_actions": list(actions),
        "repeat": repeat,
        "note": note,
    }
    if family:
        row["family"] = family
    return row


CASES: List[Dict[str, Any]] = [
    _case("knowledge.search", None, "none", _SUCCESS, verdict="advanced", ok=True,
          after={"map_id": 1, "cell": 2}, note="read answered"),
    _case("quest.list", None, "none", _SUCCESS, verdict="advanced", ok=True,
          after={"map_id": 1, "cell": 2}, note="list answered"),
    _case("farm.stop", None, "none", _SUCCESS, verdict="advanced", ok=True,
          after={"map_id": 1, "cell": 2}, note="stop acknowledged"),
    _case("inv.list", None, "none", _SUCCESS, verdict="advanced", ok=True,
          after={"map_id": 1, "cell": 2}, note="inventory read"),
    _case("hdv.prices", None, "none", _SUCCESS, verdict="advanced", ok=True,
          after={"map_id": 1, "cell": 2}, note="price cache read"),
    _case("safe.stop", None, "none", _SUCCESS, verdict="advanced", ok=True,
          after={"map_id": 1, "cell": 2}, note="stop acknowledged"),
    _case("chat.say", None, "none", _SUCCESS, verdict="advanced", ok=True,
          after={"map_id": 1, "cell": 2}, note="chat accepted"),

    _case("nav.goto", "map 698953 is not adjacent to 147063812",
          "wrong_target", _WRONG),
    _case("nav.follow", "target character 447 not found on this map",
          "wrong_target", _WRONG),
    _case("quest.advance", "npc 3888 is not on map 147063812",
          "wrong_target", _WRONG),
    _case("quest.advance", "no dialog reply matches the chosen text",
          "wrong_target", _WRONG),
    _case("quest.advance", "a previous claim ended the connection; "
                           "perform the objective instead",
          "invalid_assumption", _BAD_ASSUMPTION),
    _case("quest.advance", "the claim was ignored twice in a row",
          "plan_wrong", {"replan"}, repeat=3,
          note="third consecutive failure releases the plan"),
    _case("farm.start", "target monster 715354 is not on this map",
          "wrong_target", _WRONG),
    _case("fight.join", "no group on this map contains monster 447",
          "wrong_target", _WRONG),
    _case("fight.pass", "no live fight board: the client is not in a fight",
          "unavailable", _UNAVAILABLE),
    _case("fight.state", "no live fight board: the client is not in a fight",
          "unavailable", _UNAVAILABLE),
    _case("fight.plan", "no live fight board: the client is not in a fight",
          "unavailable", _UNAVAILABLE),
    _case("fight.estimate", "no visible group contains monster 447",
          "wrong_target", _WRONG),
    _case("inv.sell", "iron sword is not in inventory",
          "wrong_target", _WRONG),
    _case("inv.equip", "equipItem sent nothing and the item is not usable; "
                       "pass slot to send ObjectSetPositionMessage directly",
          "invalid_assumption", _BAD_ASSUMPTION),
    _case("hdv.scan", "hdv.scan needs an item, a category type or an item type id",
          "invalid_assumption", _BAD_ASSUMPTION),
    _case("hdv.list", "hdv.list needs a price: none was given and no measured "
                      "market price is cached",
          "invalid_assumption", _BAD_ASSUMPTION),
    _case("hdv.buy", "hdv.buy needs max_price (or an explicit price) so the "
                     "purchase is bounded", "invalid_assumption", _BAD_ASSUMPTION),
    _case("hdv.buy", "hdv.buy found no cached listing at or below max_price; "
                     "run hdv.scan on this item first",
          "stale_state", _WRONG),
    _case("hdv.buy", "hdv.buy refused: listing price is above max_price",
          "invalid_assumption", _BAD_ASSUMPTION, note="rule refusal stays continue"),
    _case("tap.interactive", "no visible interactive element matches the "
                             "requested id/cell/skill", "wrong_target", _WRONG),
    _case("shop.buy", None, "none", _SUCCESS, verdict="advanced", ok=True,
          after={"map_id": 1, "cell": 2}, note="tutorial pack bought"),
    _case("shop.buy", "the shop has no article to buy yet (the fake shop is still "
                      "loading its list)", "stale_state", _WRONG),
    _case("shop.buy", "the client does not have the tutorial fake shop enabled; "
                      "this button would start a real-currency purchase",
          "unavailable", _UNAVAILABLE),
    _case("shop.buy", "the shop window is not open; open it first",
          "invalid_assumption", _BAD_ASSUMPTION),
    _case("tap.interactive", "interactive element 4491 has no skillInstanceUid; "
                             "pass skill_uid", "invalid_assumption", _BAD_ASSUMPTION),
    _case("ui.tap", 'no visible element matches ".menuIconSpell"',
          "wrong_target", _WRONG),
    _case("ui.icons", None, "none", _SUCCESS, verdict="advanced", ok=True,
          after={"map_id": 1, "cell": 1}, note="menu icons listed"),
    _case("knowledge.remember", "invalid fact kind 'price_guess'",
          "invalid_assumption", _BAD_ASSUMPTION),
    _case("knowledge.forget", "fact npc:9999 not found",
          "wrong_target", _WRONG),
    _case("state.snapshot", "no client instance is running (start one in "
                            "DofusFarm first)", "unavailable", _UNAVAILABLE),
    _case("quest.advance", None, "incomplete", _WRONG, verdict="noop", ok=True,
          before={"map_id": 1, "cell": 1}, after={"map_id": 1, "cell": 1},
          note="successful call that moved nothing"),
    _case("fight.estimate", "an error phrase the taxonomy has never seen",
          "unknown", _WRONG, before={}, after={}),
]


# The entity each tool actually points at. Error texts are the harness's own
# vocabulary, never game content: the taxonomy must stay generic.
TOOL_ENTITIES = {
    "knowledge.search": "query 'bouftou'",
    "nav.goto": "map 698953",
    "nav.follow": "character 447",
    "quest.list": "quest log",
    "quest.advance": "npc 3888",
    "farm.start": "monster 715354",
    "farm.stop": "farm session",
    "fight.join": "group 447",
    "fight.pass": "fight board",
    "inv.list": "inventory",
    "inv.sell": "iron sword",
    "hdv.scan": "item 1234",
    "hdv.list": "item 1234",
    "chat.say": "channel say",
    "state.snapshot": "client dofus-01",
    "safe.stop": "session",
    "knowledge.remember": "fact npc:447",
    "knowledge.forget": "fact npc:9999",
    "fight.state": "fight board",
    "fight.plan": "fight board",
    "fight.estimate": "group 447",
    "hdv.buy": "listing 77",
    "inv.equip": "preset 12",
    "hdv.prices": "item 1234",
    "tap.interactive": "element 4491",
    "shop.buy": "the tutorial pack",
}

# Each template is a real phrase family the controller has to place in one bucket.
# The expected cause is chosen from the precondition, not from `cause_of`, so the
# agreement percentage measures the classifier rather than restating it.
_WRONG_TARGETS = [
    "{ent} is not on this map",
    "{ent} is not on map 147063812",
    "no visible group contains {ent}",
    "{ent} is not adjacent to 147063812",
    "{ent} is not in inventory",
    "no visible interactive element matches {ent}",
    "no group on this map contains {ent}",
    "no dialog reply matches {ent}",
    "unknown {ent} id",
    "{ent} not found",
    "no such {ent}",
    "invalid id for {ent}",
    "{ent} is too far from the character",
]
_INVALID = [
    "schema rejected the call (unknown argument(s) for {tool}: objective_id)",
    "{tool} needs one of: item, category, type",
    "{tool} not_mapped for this objective",
    "unknown tool {tool}",
    "{tool} offers no replies",
    "{tool} needs max_price before it can buy",
    "{tool} needs an explicit price",
    "{tool} needs a saved preset",
    "equipItem sent nothing",
    "price is above max_price",
    "{tool} element has no skillInstanceUid",
    "hdv.scan needs an item or a type",
    "invalid fact kind 'price_guess'",
]
_STALE = [
    "cannot read properties of null (reading 'id')",
    "TypeError: dialogue is not a function",
    "ReferenceError: scene is not defined",
    "preset blocked: the client is mid-scene",
    "no cached listing for this item",
    "cannot read properties of undefined (reading 'mapId')",
    "TypeError: reward is not iterable",
    "preset blocked while the scene loads",
    "{ent} was read from an unreadable snapshot",
    "the client returned a null dialog object",
    "a replayed snapshot could not be attributed",
    "the page threw while reading {ent}",
    "no cached listing at or below the bound",
]
_UNAVAILABLE = [
    "no client instance is running (start one in DofusFarm first)",
    "instance dofus-01 is not an active client window",
    "not in a fight",
    "no live fight board: the client is not in a fight",
    "no local handler for {tool}",
    "no sentinel handler for {tool}",
    "no simulator for tool {tool}",
    "no client instance answers for dofus-02",
    "instance dofus-03 is not an active client",
    "the client window for dofus-04 is not active",
    "no live fight board for {ent}",
    "not in a fight: there is no board to read",
    "no client instance is running for {ent}",
]
_INCOMPLETE = [
    "the call answered but the world did not move",
    "a successful call changed no cell, map or HP",
    "the objective is still open after the call",
    "the action landed but the target did not change",
    "the step moved kamas but not the goal",
    "the client reported success with no effect",
    "the walk ended on the same cell",
    "the dialog opened but nothing was accepted",
    "the loot changed but the objective did not",
    "the call was acknowledged and ignored",
    "the map stayed the same after the crossing",
    "the inventory changed but the delivery did not",
    "the turn passed without changing the board",
]
_UNKNOWN = [
    "an opaque failure the taxonomy has never seen",
    "the client said no for an unrecorded reason",
    "an unclassified refusal from {tool}",
    "a failure with no text and no delta",
    "the call ended without a note",
    "an unrecognised client outcome",
    "a silent failure with no observation",
    "the bridge returned an unlabelled error",
    "a failure only the future taxonomy can name",
    "no note, no delta, no classification",
    "an unclassified outcome from {ent}",
    "the client failed in a way not yet modelled",
    "a failed call with no attributable evidence",
]
_PLAN_WRONG = [
    "the action did not land",
    "{ent} resisted the action",
    "the call timed out while acting on {ent}",
    "the action was refused in-game",
    "the dialogue did not open for {ent}",
    "the skill resisted on {ent}",
    "the mob moved before the action landed",
    "the client answered 503 while acting",
    "the action was aborted by the scene",
    "the target changed during the action",
    "the call failed twice for different reasons",
    "the plan asked the wrong tool three times",
    "the same failure repeated without a new note",
]
_CAUSE_ACTIONS = {
    "none": {"continue"},
    "wrong_target": {"retry", "recover", "replan"},
    "stale_state": {"retry", "recover", "replan"},
    "incomplete": {"retry", "recover", "replan"},
    "invalid_assumption": {"replan", "continue"},
    "plan_wrong": {"replan"},
    "unavailable": {"replan"},
    "unknown": {"retry", "recover", "replan"},
}


def _generated_cases() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for tool in tool_names():
        ent = TOOL_ENTITIES.get(tool, "target 1")
        for i in range(13):
            rows.append(_case(tool, None, "none", _CAUSE_ACTIONS["none"],
                              verdict="advanced", ok=True,
                              after={"map_id": 1 + i, "cell": 2 + i},
                              note="successful step %d" % i, family="none:%d" % i))
            rows.append(_case(tool, _WRONG_TARGETS[i].format(tool=tool, ent=ent),
                              "wrong_target", _CAUSE_ACTIONS["wrong_target"],
                              family="wrong_target:%d" % i))
            rows.append(_case(tool, _INVALID[i].format(tool=tool, ent=ent),
                              "invalid_assumption", _CAUSE_ACTIONS["invalid_assumption"],
                              family="invalid_assumption:%d" % i))
            rows.append(_case(tool, _STALE[i].format(tool=tool, ent=ent),
                              "stale_state", _CAUSE_ACTIONS["stale_state"],
                              family="stale_state:%d" % i))
            rows.append(_case(tool, _UNAVAILABLE[i].format(tool=tool, ent=ent),
                              "unavailable", _CAUSE_ACTIONS["unavailable"],
                              family="unavailable:%d" % i))
            before = {"map_id": 1, "cell": 1}
            after = ({"map_id": 1, "cell": 1} if i % 2 == 0
                     else {"map_id": 1, "cell": 1, "kamas": 100 + i})
            rows.append(_case(tool, _INCOMPLETE[i].format(tool=tool, ent=ent),
                              "incomplete", _CAUSE_ACTIONS["incomplete"],
                              verdict="noop", ok=True, before=before, after=after,
                              note="partial or absent world change %d" % i,
                              family="incomplete:%d" % i))
            rows.append(_case(tool, _UNKNOWN[i].format(tool=tool, ent=ent),
                              "unknown", _CAUSE_ACTIONS["unknown"],
                              before={}, after={}, family="unknown:%d" % i))
            rows.append(_case(tool, _PLAN_WRONG[i].format(tool=tool, ent=ent),
                              "plan_wrong", _CAUSE_ACTIONS["plan_wrong"],
                              repeat=3, note="third consecutive failure",
                              family="plan_wrong:%d" % i))
    return rows


def manifest_for(cases: List[Dict[str, Any]], kind: str,
                 agreement: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "rows": len(cases),
        "tools": dict(Counter(case["tool"] for case in cases)),
        "tool_count": len({case["tool"] for case in cases}),
        "causes": dict(Counter(case["expected_cause"] for case in cases)),
        "missing_tools": sorted(set(tool_names()) - {c["tool"] for c in cases}),
        "missing_causes": sorted(set(CAUSES) - {c["expected_cause"] for c in cases}),
        "kind": kind,
        "live": False,
    }
    if agreement is not None:
        manifest["agreement"] = agreement
    return manifest


def measure(cases: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, Any]]:
    failures: List[str] = []
    cause_n = cause_ok = action_n = action_ok = 0
    for index, case in enumerate(cases):
        controller = Controller()
        decision = None
        for _ in range(max(1, int(case["repeat"]))):
            verdict = Verdict(case["verdict"], 0.9, None, False, "rule")
            decision = controller.on_step(
                verdict, case["tool"], bool(case["ok"]), case["error"],
                case["before"], case["after"], detail=case["detail"])
        got = decision.cause if decision is not None else "none"
        cause_n += 1
        if got == case["expected_cause"]:
            cause_ok += 1
        else:
            failures.append("%d %s cause %s != %s | %s"
                            % (index, case["tool"], got,
                               case["expected_cause"], (case["error"] or "")[:60]))
        action_n += 1
        if decision is not None and decision.action in case["expected_actions"]:
            action_ok += 1
        else:
            failures.append("%d %s action %s not in %s"
                            % (index, case["tool"],
                               getattr(decision, "action", None),
                               case["expected_actions"]))
    agreement = {
        "cause": round(cause_ok / cause_n, 4) if cause_n else 0.0,
        "action": round(action_ok / action_n, 4) if action_n else 0.0,
        "cause_rows": cause_n,
        "action_rows": action_n,
    }
    return failures, agreement


def build() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    return CASES, manifest_for(CASES, "constraint_test_corpus")


def check() -> Tuple[List[str], Dict[str, Any]]:
    failures, agreement = measure(CASES)
    return failures, manifest_for(CASES, "constraint_test_corpus", agreement)


def write_cases(cases: List[Dict[str, Any]], out: Path, kind: str,
                agreement: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    manifest = manifest_for(cases, kind, agreement)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    (out.parent / (out.stem + ".manifest.json")).write_text(
        json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return manifest


def load_corpus(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _report(label: str, failures: List[str], manifest: Dict[str, Any]) -> bool:
    agreement = manifest.get("agreement") or {}
    print("\n%s: %d rows, %d tools, causes %s"
          % (label, manifest["rows"], manifest.get("tool_count", 0),
             json.dumps(manifest["causes"], sort_keys=True)))
    if agreement:
        print("  agreement: cause %.4f, action %.4f"
              % (agreement.get("cause", 0.0), agreement.get("action", 0.0)))
    if manifest.get("missing_tools"):
        print("  MISSING TOOLS:", manifest["missing_tools"])
    if manifest.get("missing_causes"):
        print("  MISSING CAUSES:", manifest["missing_causes"])
    if failures:
        print("  FAILED (%d), first 12:" % len(failures))
        for row in failures[:12]:
            print("   ", row)
        return False
    print("  all cases routed as expected")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--generate", action="store_true",
                        help="write the 25-tool x 8-cause synthetic corpus")
    parser.add_argument("--corpus", type=Path, default=None,
                        help="validate a JSONL corpus instead of the built-ins")
    args = parser.parse_args(argv)

    ok = True
    if args.corpus is not None:
        rows = load_corpus(args.corpus)
        failures, agreement = measure(rows)
        ok = _report(str(args.corpus), failures,
                     manifest_for(rows, "constraint_synthetic", agreement)) and ok
    elif args.generate:
        rows = _generated_cases()
        failures, agreement = measure(rows)
        manifest = write_cases(rows, args.out, "constraint_synthetic", agreement)
        print("wrote", args.out)
        ok = _report("generated", failures, manifest) and ok
    else:
        failures, manifest = check()
        ok = _report("built-in", failures, manifest) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
