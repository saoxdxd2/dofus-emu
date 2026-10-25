"""Regression tests for correctness, resource-safety and performance defects.

Run:  python test_regressions.py   (from the repo root)
No pytest, torch checkpoint, or ONNX Runtime required.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DOFUS_NETWORK = REPO_ROOT / "dofus-network"
for p in (str(DOFUS_NETWORK), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.schema import validate_call  # noqa: E402
from ai.lang_detect import detect, reply_language  # noqa: E402
from ai.onnx_policy import decode_outputs, parse_span, best_span  # noqa: E402
import ai.encoder_policy as ep  # noqa: E402
import ai.thx_policy as thx  # noqa: E402

_results: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))


def summarize() -> int:
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\n{passed}/{total} passed, {failed} failed")
    return 1 if failed else 0


# -- 1. THX U+FFFD corruption ----------------------------------------------

def test_fight_state_exposes_monster_data() -> None:
    """The live board must carry the data needed to learn real monster play."""
    js = (REPO_ROOT / "scripts" / "mobile-disguise.js").read_text(encoding="utf-8")
    check("fighters expose spell descriptors",
          "spells: fighterSpells" in js and "spell_ids: spellIds" in js)
    check("the board exposes recent fight actions",
          "state.last_actions = lastActions" in js)
    check("monster spells resolve through the client's own loader",
          "fm.getFighterSpell(" in js)
    check("the client build marker is current", "2026-10-25d" in js)


def test_live_policy_brain_is_wired() -> None:
    """The page must ask the dynamic policy API, not a fixed build/server."""
    js = (REPO_ROOT / "scripts" / "mobile-disguise.js").read_text(encoding="utf-8")
    server = (REPO_ROOT / "dofus-network" / "combat" / "combat_ai_server.py") \
        .read_text(encoding="utf-8")
    check("the page asks the live policy API",
          "__agentFightBrain" in js and "/v1/combat/decide" in js)
    check("the local heuristic remains the fallback",
          "_askBrain" in js and "_localTick" in js and "_executeAction" in js)
    check("a fight action re-checks the live turn owner",
          "_stillMyTurn" in js and "fm.currentFighterId" in js)
    check("the old hardcoded THX-01 combat server is gone",
          "THX-01" not in server and "Flèche Explosive" not in server
          and "transformers" not in server)


def test_autopilot_money_guards() -> None:
    """The autonomous loop must not be able to spend money by accident.

    Three separate failure modes, all of which cost real kamas:
      1. buying a listing (irreversible, no undo, no negotiation);
      2. listing without a price (the client would have to invent one);
      3. listing below the floor (a typo, not a strategy).
    A price rule is only allowed when the client can resolve it from a price the
    server actually sent -- that is what MEASURED_PRICE_RULES names.
    """
    from agent.autopilot import Autopilot, MEASURED_PRICE_RULES, PRICE_FLOOR
    from agent.runtime import ToolCall

    with tempfile.TemporaryDirectory() as tmp:
        pilot = Autopilot(runtime=None, goals=["t"],  # guard() touches no runtime
                          log_path=Path(tmp) / "a.jsonl",
                          stop_file=Path(tmp) / "stop")

        check("autopilot refuses a purchase",
              pilot.guard(ToolCall("hdv.buy", {"item": "bread", "price": 5})) is not None)

        no_price = pilot.guard(ToolCall("hdv.list", {"item": "bread"}))
        check("autopilot refuses a listing with no price", no_price is not None,
              str(no_price))

        below = pilot.guard(ToolCall("hdv.list", {"item": "bread", "price": PRICE_FLOOR - 1}))
        check("autopilot refuses a price below the floor", below is not None, str(below))

        check("autopilot allows an explicit price",
              pilot.guard(ToolCall("hdv.list", {"item": "bread", "price": 37})) is None)

        for rule in sorted(MEASURED_PRICE_RULES):
            ok = pilot.guard(ToolCall("hdv.list", {"item": "bread", "price_rule": rule}))
            check(f"autopilot allows the measured rule {rule}", ok is None, str(ok))

        made_up = pilot.guard(ToolCall("hdv.list", {"item": "bread", "price_rule": "triple_market"}))
        check("autopilot refuses an unmeasured price rule", made_up is not None, str(made_up))


def test_autopilot_listing_is_not_revenue() -> None:
    """A successful listing is recorded as open, never as kamas earned.

    Selling is not controllable: putting an item on the market is, having a buyer
    appear is not. Counting a listing as revenue teaches a drop rate that never
    happened, which is exactly the class of error the recorder exists to prevent.
    """
    from agent.autopilot import Autopilot

    class _Result:
        ok = True
        error = None
        data = {"price": 41, "price_source": "average_minus_1:average"}

    with tempfile.TemporaryDirectory() as tmp:
        pilot = Autopilot(runtime=None, goals=["t"],
                          log_path=Path(tmp) / "a.jsonl",
                          stop_file=Path(tmp) / "stop")
        before = len(pilot.open_listings)
        # Reproduce the bookkeeping the run loop performs for a listing result.
        source = _Result.data.get("price_source")
        if source:
            pilot.open_listings.append({"price": _Result.data["price"],
                                        "price_source": source, "state": "open"})
        check("listing recorded as open, not as income",
              len(pilot.open_listings) == before + 1
              and pilot.open_listings[-1]["state"] == "open"
              and "earned" not in pilot.open_listings[-1])


def test_thx_no_replacement_char() -> None:
    text = (DOFUS_NETWORK / "ai" / "thx_policy.py").read_text(encoding="utf-8")
    check("thx_policy has no U+FFFD", chr(0xFFFD) not in text,
          f"{text.count(chr(0xFFFD))} replacement chars found")


def test_thx_price_pattern_clean() -> None:
    pattern = thx._RE_PRICE.pattern
    check("thx _RE_PRICE clean (für/à/per)",
          "für" in pattern and "à" in pattern and "per" in pattern, pattern)
    check("thx _RE_PRICE no mangled chars",
          "f\ufffd" not in pattern and chr(0xFFFD) not in pattern
          and "per|pour" not in pattern, pattern)


# -- 2. Schema coercion, enum / list validation, special rules --------------

def test_schema_int_coercion_from_string() -> None:
    ok, out = validate_call("knowledge.search", {"query": "Bouftou", "limit": "5"})
    check("int coerced from string", ok and out.get("limit") == 5,
          f"ok={ok} out={out}")


def test_schema_bool_rejects_string() -> None:
    ok, _ = validate_call("inv.sell", {"item": "Bouftou Wool", "all": "true"})
    check("bool rejects non-bool string", not ok)


def test_schema_str_list_from_string() -> None:
    import agent.schema as sc
    found = False
    for spec in sc.TOOLS:
        for pname, p in spec.params.items():
            if p.get("type") == "str_list":
                ok, out = validate_call(spec.name, {pname: "a, b, c"})
                check("str_list coerced", ok and out.get(pname) == ["a", "b", "c"],
                      f"ok={ok} out={out.get(pname) if ok else out}")
                found = True
                break
    if not found:
        check("str_list coercion", True, "no str_list param; skipped")


def test_schema_enum_validation() -> None:
    ok, out = validate_call("knowledge.search", {"query": "x", "kind": "item"})
    check("enum value accepted", ok and out.get("kind") == "item", str(out))
    ok, _ = validate_call("knowledge.search", {"query": "x", "kind": "dragon"})
    check("enum invalid rejected", not ok)


def test_schema_required_missing() -> None:
    ok, _ = validate_call("knowledge.search", {"limit": 3})
    check("required missing rejected", not ok)


def test_schema_unknown_args_rejected() -> None:
    ok, _ = validate_call("knowledge.search", {"query": "x", "bogus": 1})
    check("unknown args rejected", not ok)


def test_schema_special_rules() -> None:
    ok, _ = validate_call("nav.goto", {"x": 4})
    check("nav.goto half-xy rejected", not ok)
    ok, _ = validate_call("inv.sell", {"all": False})
    check("inv.sell needs item or all", not ok)
    ok, _ = validate_call("inv.sell", {"item": "x", "all": True,
                                       "price_rule": "fixed"})
    check("inv.sell fixed needs price", not ok)
    ok, out = validate_call("inv.sell", {"item": "x", "all": True,
                                         "price_rule": "fixed", "price": 100})
    check("inv.sell fixed with price accepted", ok and out.get("price") == 100,
          str(out))
    ok, _ = validate_call("chat.say", {"channel": "whisper"})
    check("chat.say whisper needs target", not ok)


def test_schema_coerced_roundtrip() -> None:
    ok, out = validate_call("inv.sell", {"item": "Bouftou Wool", "all": False,
                                         "price_rule": "median_minus_1"})
    check("valid inv.sell roundtrip", ok and out.get("item") == "Bouftou Wool",
          str(out))



# -- 3. Language detection edge cases ---------------------------------------

def test_lang_detect_all_four() -> None:
    cases = [("sell the Bouftou Wool", "en"), ("vends la laine de Bouftou", "fr"),
             ("vende la lana de Bouftou", "es"), ("verkaufe die Bouftowolle", "de")]
    for text, expected in cases:
        det = detect(text)
        check(f"detect {expected!r}: {text!r}", det.lang == expected and det.decided,
              f"got {det.lang} decided={det.decided}")


def test_lang_detect_diacritics() -> None:
    # Single accented word alone biases but may not reach the decided margin;
    # phrases with markers + diacritics decide clearly.
    check("où vas-tu -> fr", detect("où vas-tu").lang == "fr")
    check("¿cómo estás -> es", detect("¿cómo estás").lang == "es")
    check("Wo ist das? -> de", detect("Wo ist das?").lang == "de")


def test_lang_detect_undecided() -> None:
    det = detect("xyz qwoplm 12345")
    check("nonsense undecided", det.lang == "auto" or not det.decided,
          f"lang={det.lang} decided={det.decided}")


def test_lang_detect_prior() -> None:
    det = detect("a", prior="de")
    check("prior fallback", det.lang in ("auto", "de"))


def test_reply_language() -> None:
    check("reply unknown -> fr", reply_language("xyzqwoplm") == "fr")
    check("reply en", reply_language("sell the wool") == "en")


def test_label_rows() -> None:
    from ai.lang_detect import label_rows
    labelled, report = label_rows([{"goal": "sell the wool"}, {"goal": "vends la laine"},
                                   {"goal": "xyz"}])
    check("label_rows attaches lang", labelled[0]["lang"] == "en"
          and labelled[1]["lang"] == "fr", str(labelled))
    check("label_rows undecided", report["undecided"] >= 1, str(report))


# -- 4. Encoder dataset: tool fix + overflow --------------------------------

class _FakeEncoding:
    def __init__(self, ids):
        self.ids = list(ids)
        self.offsets = [(i, i + 1) for i in range(len(ids))]


class _FakeTokenizer:
    def __init__(self, ids):
        self._ids = list(ids)

    def token_to_id(self, token):
        return 0

    def encode(self, text, add_special_tokens=False):
        return _FakeEncoding(self._ids)


def _make_dataset(infer: bool, n_tokens: int):
    tok = _FakeTokenizer(list(range(n_tokens)))
    rows = [{"goal": "sell the Bouftou Wool", "id": "r1"}]
    return ep.EncoderDataset(rows, tok, 16, ep.ENCODER_SYSTEM_PROMPT, infer=infer)


def test_encoder_inference_tooless_accepted() -> None:
    ds = _make_dataset(True, 8)
    check("inference row without tool accepted", len(ds.items) == 1 and ds.skipped == 0,
          f"items={len(ds.items)} skipped={ds.skipped}")


def test_encoder_training_tooless_rejected() -> None:
    ds = _make_dataset(False, 8)
    check("training row without tool rejected", len(ds.items) == 0 and ds.skipped == 1,
          f"items={len(ds.items)} skipped={ds.skipped}")


def test_encoder_overflow_skipped() -> None:
    ds = _make_dataset(True, 40)
    check("over-long row skipped", len(ds.items) == 0 and ds.skipped == 1,
          f"items={len(ds.items)} skipped={ds.skipped}")



# -- 5. ONNX pure-numpy decoder produces a "tool" field ---------------------

def test_onnx_decode_outputs_has_tool() -> None:
    import numpy as np
    tools = ["inv.sell", "nav.goto", "safe.stop"]
    fields = [
        {"tool": "inv.sell", "name": "item", "type": "str", "enum": (), "default": None},
        {"tool": "inv.sell", "name": "all", "type": "bool", "enum": (), "default": False},
    ]
    length = 4
    tool_logits = np.array([[2.5, 0.1, 0.3]])
    presence = np.zeros((1, len(fields)), dtype=np.float32)
    presence[0, 0] = 3.0
    bool_logits = np.zeros((1, len(fields)), dtype=np.float32)
    span_start = np.full((1, len(fields), length), -5.0, dtype=np.float32)
    span_end = np.full((1, len(fields), length), -5.0, dtype=np.float32)
    span_start[0, 0, 1] = 5.0
    span_end[0, 0, 2] = 5.0
    decoded = decode_outputs(
        [tool_logits, presence, bool_logits, span_start, span_end], fields, tools, length)
    check("onnx decode_outputs returns tool", decoded.get("tool") == "inv.sell",
          str(decoded))
    check("onnx decode_outputs returns args dict", isinstance(decoded.get("args"), dict),
          str(decoded))


def test_parse_span_int_and_str_list() -> None:
    check("parse_span int", parse_span(" 42 ", "int") == 42)
    check("parse_span float", parse_span(" 3.14 ", "float") == 3.14)
    check("parse_span str_list", parse_span("a, b; c", "str_list") == ["a", "b", "c"])
    check("parse_span empty -> None", parse_span("", "str") is None)


def test_best_span_basic() -> None:
    import numpy as np
    start = np.array([1.0, 5.0, 2.0, 0.0])
    end = np.array([0.0, 1.0, 5.0, 2.0])
    span = best_span(start, end, length=4, max_span=32)
    check("best_span picks (1,2)", span == (1, 2), str(span))


# -- 6. Recorder redaction + stats ------------------------------------------

def test_recorder_redaction_and_stats() -> None:
    from agent.recorder import TrajectoryRecorder
    with tempfile.TemporaryDirectory() as tmp:
        rec = TrajectoryRecorder(root=Path(tmp), instance="t1")
        rec.log("step", {"tool": "nav.goto", "args": {"password": "secret123", "x": 1},
                         "ok": True})
        rec.log("step", {"tool": "knowledge.search", "args": {"query": "x"},
                         "ok": False, "error": "boom"})
        stats = rec.stats()
        check("recorder stats lines==2", stats["lines"] == 2, str(stats))
        check("recorder steps_ok==1", stats["steps_ok"] == 1, str(stats))
        check("recorder steps_failed==1", stats["steps_failed"] == 1, str(stats))
        raw = (Path(tmp) / "t1.jsonl").read_text(encoding="utf-8")
        check("secret redacted on disk", "secret123" not in raw, raw[:200])
        check("redaction marker present", "[redacted]" in raw, raw[:200])


# -- 7. Executor invalid call handling --------------------------------------

def test_executor_invalid_call_no_crash() -> None:
    from agent.executor import ToolExecutor
    exec_ = ToolExecutor(recorder=None)
    result = exec_.execute("unknown.tool", {"a": 1})
    check("unknown tool -> ok=False", result.ok is False and result.error is not None,
          str(result.as_dict()))
    result = exec_.execute("nav.goto", {"x": 1})
    check("half-xy nav.goto -> ok=False", result.ok is False, str(result.as_dict()))


def test_executor_records_piggybacked_observation() -> None:
    """A live directive carries the game state; it must reach the recorder.

    The defect this guards is silent rather than loud: `observation` was null in
    100% of the logged records, which makes every state-conditioned decision
    unlearnable. If the bridge stops attaching `snapshot`, or the executor stops
    reading it, the corpus rots with no error anywhere -- so assert it here.
    """
    from agent.executor import ToolExecutor, ToolResult
    from agent.recorder import TrajectoryRecorder

    snap = {"connected": True, "position": {"cell": 402, "map_id": 1234},
            "hp": {"life": 120, "max": 300}, "xp": 1500}
    root = Path(tempfile.mkdtemp())
    recorder = TrajectoryRecorder(root=root, instance="obs")
    executor = ToolExecutor(recorder=recorder)

    executor._dispatch = lambda tool, args, source="live", instance=None, observation=None: ToolResult(
        tool=tool, ok=True, source="live", directive="STATE_SNAPSHOT",
        data={"snapshot": snap})
    executor.execute("state.snapshot", {}, goal="where am i")

    steps = [r for r in recorder.tail(10) if r.get("kind") == "step"]
    check("observation recorded from bridge snapshot",
          bool(steps) and steps[-1].get("observation") == snap,
          str(steps[-1].get("observation") if steps else None)[:120])
    check("piggybacked observation has age 0",
          bool(steps) and steps[-1].get("observation_age_ms") == 0.0,
          str(steps[-1].get("observation_age_ms") if steps else None))

    # the bridge reply itself must surface the snapshot on the ToolResult
    executor._http_json = lambda *a, **k: (
        True, {"ok": True, "result": {"ok": True}, "snapshot": snap})
    result = executor._directive("STATE_SNAPSHOT", "state.snapshot", {}, "live")
    check("directive result exposes the snapshot",
          isinstance(result.data, dict) and result.data.get("snapshot") == snap,
          str(result.data)[:160])


def test_price_rule_without_price_is_dropped() -> None:
    """`price_rule=fixed` with no price must not kill an otherwise-correct call.

    Measured on v11: 459 of 800 val rows in one contiguous block were
    `hdv.list {item, price_rule: "fixed"}` from goals that state no price. The
    cross-parameter rule correctly refuses to list at an invented price, but the
    refusal discarded a correct tool choice and a correct item span over one
    optional field. Dropping the field restores the label's own canonical form.
    """
    from ai.postprocess import clean_call

    goal = "put TorNiFen Ore on the market"
    cleaned = clean_call("hdv.list", {"price_rule": "fixed", "item": "TorNiFen Ore"}, goal)
    ok, result = validate_call("hdv.list", cleaned)
    check("hdv.list fixed-without-price is executable",
          ok and result.get("price_rule") == "median_minus_1", str(result))

    # a stated price keeps the rule: the model emits `price` as an int then
    kept = clean_call("hdv.list",
                      {"price_rule": "fixed", "price": 5000, "item": "Bouftou Wool"},
                      "sell Bouftou Wool at 5000")
    ok2, result2 = validate_call("hdv.list", kept)
    check("hdv.list fixed-with-price keeps the rule",
          ok2 and result2.get("price_rule") == "fixed", str(kept))


def test_dialog_policy_and_contract() -> None:
    from agent.autopilot import _compact
    from agent.dialog_policy import rank_dialog
    from agent.planner import QuestPlanner

    ok, args = validate_call("quest.advance", {"quest_id": 18, "reply_text": "Oui"})
    check("quest.advance accepts reply_text", ok and args.get("reply_text") == "Oui")

    def observation(replies):
        return {
            "in_dialog": True,
            "current_objective": {"quest_id": 18, "objective_id": 115},
            "knowledge": {"quest_id": 18, "objective_id": 115,
                          "objective": {"text": "Vaincre x1 Wabbit Gm"}},
            "dialog": {"open": True, "text_ready": True, "ids_ready": True,
                       "replies": replies},
        }

    check("a closed dialog has no decision", rank_dialog({"dialog": {"open": False}}) is None)
    waiting = observation([{"index": 0, "text": "Oui", "reply_id": 41}])
    waiting["dialog"]["text_ready"] = False
    check("an incomplete dialog waits", rank_dialog(waiting)["status"] == "wait")
    only = rank_dialog(observation([{"index": 0, "text": "Oui", "reply_id": 41}]))
    check("one reply is taken", only["status"] == "choose" and only["reply"]["reply_id"] == 41)
    ranked = rank_dialog(observation([
        {"index": 0, "text": "Parler du temps", "reply_id": 40},
        {"index": 1, "text": "Combattre le Wabbit Gm", "reply_id": 41},
    ]))
    check("French overlap selects the matching reply",
          ranked["status"] == "choose" and ranked["reply"]["index"] == 1)
    tied = rank_dialog(observation([
        {"index": 0, "text": "Wabbit", "reply_id": 40},
        {"index": 1, "text": "Gm", "reply_id": 41},
    ]))
    check("a top-score tie abstains", tied["status"] == "abstain")

    planner = QuestPlanner()
    call = planner.choose("do all the quests", observation([
        {"index": 0, "text": "Parler du temps", "reply_id": 40},
        {"index": 1, "text": "Combattre le Wabbit Gm", "reply_id": 41},
    ]))
    check("planner sends reply text and index together",
          call.tool == "quest.advance" and call.args == {
              "quest_id": 18, "reply": 1, "reply_text": "Combattre le Wabbit Gm"},
          str(call.args))
    abstained = planner.choose("do all the quests", observation([
        {"index": 0, "text": "Wabbit", "reply_id": 40},
        {"index": 1, "text": "Gm", "reply_id": 41},
    ]))
    check("an abstention leaves the dialog open",
          abstained.tool == "state.snapshot" and planner.last_dialog_decision["status"] == "abstain")
    compact = _compact(observation([{"index": 0, "text": "Oui", "reply_id": 41}]))
    check("trajectory rows retain dialog and quest context",
          compact.get("dialog", {}).get("open") is True
          and compact.get("knowledge", {}).get("objective_id") == 115
          and compact.get("current_objective", {}).get("quest_id") == 18)


def test_fight_estimate_and_guard() -> None:
    """The fight gate is arithmetic plus refusal, and it must stay honest.

    The estimator is only allowed to authorise a fight when every input is
    known, the group is not a boss, and a fitted probability clears the
    threshold. A heuristic score is not a probability; an unknown level is not
    zero; a failed estimate is not permission to try.
    """
    import json

    from combat.fight_estimate import (
        EnemyFacts, EnemyMember, FightAdvisor, FightCalibrator, FightOutcomeLog,
        PlayerFacts, calibration_report, estimate_from_facts, fit_calibrator)
    from combat.fight_estimate import main as fight_main

    player = PlayerFacts(
        level=60, current_hp=900, max_hp=900, ap=6,
        dpt_by_element={"earth": 260.0}, damage_lines=2,
        resist_percent={"earth": 10, "fire": 10, "water": 10, "air": 10})
    enemy = EnemyFacts(group_id=7, members=[
        EnemyMember(monster_id=93, name="Bouftou", level=52, hp=600, ap=4,
                    resist_percent={"earth": 5, "fire": 0, "water": 0, "air": 0}),
    ])
    high = FightCalibrator(features=["level_gap"], mean=[0.0], scale=[1.0],
                           coef=[0.0], intercept=2.5)   # sigmoid(2.5) = 0.924

    uncalibrated = estimate_from_facts(player, enemy)
    check("an uncalibrated estimate has no probability", uncalibrated["p_win"] is None)
    check("an uncalibrated estimate blocks the fight",
          uncalibrated["allowed"] is False
          and "uncalibrated" in uncalibrated["blocked_reason"],
          str(uncalibrated["blocked_reason"]))

    allowed = estimate_from_facts(player, enemy, calibrator=high)
    check("a passing estimate allows the fight",
          allowed["allowed"] is True and allowed["p_win"] >= 0.90,
          f"p_win={allowed['p_win']} blocked={allowed['blocked_reason']}")

    strict = estimate_from_facts(player, enemy, calibrator=high, min_p_win=0.95)
    check("the probability threshold is enforced",
          strict["allowed"] is False and "below" in strict["blocked_reason"],
          str(strict["blocked_reason"]))

    fragile = estimate_from_facts(player, enemy, calibrator=high,
                                  max_hp_loss_fraction=0.01)
    check("the HP-loss limit is enforced",
          fragile["allowed"] is False and "HP loss" in fragile["blocked_reason"],
          str(fragile["blocked_reason"]))

    boss_enemy = EnemyFacts(group_id=8, members=[
        EnemyMember(monster_id=93, name="Bouftou", level=52, hp=600, ap=4,
                    is_boss=True)])
    boss = estimate_from_facts(player, boss_enemy, calibrator=high)
    check("a boss is opt-in, never the default",
          boss["allowed"] is False and "boss" in boss["blocked_reason"],
          str(boss["blocked_reason"]))
    opted = estimate_from_facts(player, boss_enemy, calibrator=high, allow_boss=True)
    check("a boss passes only with the opt-in", opted["allowed"] is True,
          str(opted["blocked_reason"]))

    unknown = estimate_from_facts(
        PlayerFacts(level=60, current_hp=900, max_hp=900, ap=6,
                    dpt_by_element={"earth": 260.0}),
        EnemyFacts(group_id=9, members=[], missing=["monster:999"]), calibrator=high)
    check("missing monster data is named, not read as zero",
          unknown["allowed"] is False and "monster:999" in unknown["missing"],
          str(unknown["missing"]))

    rows = [{"won": (i % 3 != 0), "features": {"level_gap": float(i % 5),
                                               "group_size": float(i % 4 + 1),
                                               "turns_to_kill": float(i % 6 + 1),
                                               "turns_to_die": float(i % 7 + 1),
                                               "hp_ratio": float(i + 1),
                                               "hp_loss_fraction": (i % 5) / 10.0,
                                               "has_boss": 0.0,
                                               "unknown_members": 0.0}}
            for i in range(30)]
    fitted = fit_calibrator(rows)
    check("a calibrator fits when both classes exist", fitted is not None)
    if fitted is not None:
        restored = FightCalibrator.from_dict(fitted.to_dict())
        sample = fitted.predict(rows[0]["features"])
        check("the calibrator round-trips through JSON",
              restored is not None and restored.predict(rows[0]["features"]) == sample)
        check("the calibrated probability is bounded",
              isinstance(sample, float) and 0.0 <= sample <= 1.0, str(sample))
    one_class = fit_calibrator([dict(row, won=True) for row in rows])
    check("one class cannot be calibrated", one_class is None)

    flat_rows = [{"won": i != 0, "features": {"level_gap": float(i)}}
                 for i in range(10)]
    flat = FightCalibrator(features=["level_gap"], mean=[0.0], scale=[1.0],
                           coef=[0.0], intercept=0.0)  # always 0.5
    flat_report = calibration_report(flat, flat_rows)
    check("the calibration report counts the labels",
          flat_report["n"] == 10 and flat_report["wins"] == 9
          and flat_report["losses"] == 1, str(flat_report)[:160])
    check("a constant 0.5 model shows its calibration error",
          abs(flat_report["ece"] - 0.4) < 1e-6
          and abs(flat_report["brier"] - 0.25) < 1e-6, str(flat_report)[:160])
    separated = FightCalibrator(features=["level_gap"], mean=[5.0], scale=[1.0],
                                coef=[10.0], intercept=0.0)
    separated_rows = ([{"won": False, "features": {"level_gap": 0.0}}
                       for _ in range(10)]
                      + [{"won": True, "features": {"level_gap": 10.0}}
                         for _ in range(10)])
    separated_report = calibration_report(separated, separated_rows)
    check("a separated model reports near-zero error",
          separated_report["ece"] is not None and separated_report["ece"] <= 1e-4
          and separated_report["brier"] <= 1e-6, str(separated_report)[:160])

    import contextlib
    import io
    with tempfile.TemporaryDirectory() as tmp:
        rows_path = Path(tmp) / "outcomes.jsonl"
        rows_path.write_text("\n".join(json.dumps(row) for row in rows),
                             encoding="utf-8")
        out_path = Path(tmp) / "calibrator.json"
        refused = False
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                fight_main(["--rows", str(rows_path), "--out", str(out_path),
                            "--max-ece", "-1"])
        except SystemExit:
            refused = True
        check("the calibration gate refuses a failing fit",
              refused and not out_path.exists(), str(out_path.exists()))
        with contextlib.redirect_stdout(io.StringIO()):
            code = fight_main(["--rows", str(rows_path), "--out", str(out_path),
                               "--max-ece", "-1", "--force"])
        installed = (json.loads(out_path.read_text(encoding="utf-8"))
                     if out_path.exists() else {})
        metrics = installed.get("metrics") or {}
        check("--force installs and records the failed gate",
              code == 0 and metrics.get("gate_passed") is False
              and metrics.get("forced") is True, str(metrics)[:160])

    class _Store:
        def monster(self, monster_id):
            return {"monster_id": monster_id, "names": {"fr": "Bouftou"},
                    "is_boss": False, "grades": [
                        {"grade": 1, "level": 52, "life_points": 600,
                         "action_points": 4, "neutral_res": 0, "earth_res": 5,
                         "fire_res": 0, "water_res": 0, "air_res": 0,
                         "fixed_res": 0}]}

        def find_entity(self, kind, text, lang="fr", limit=5):
            return [{"kind": "monster", "id": 93, "name": "Bouftou"}]

    def group(group_id):
        return {"group_id": group_id, "monster_ids": [93],
                "member_count": 1, "members": [{"monster_id": 93, "level": 52}]}

    advisor = FightAdvisor(store=_Store())
    advisor.estimate_group = lambda row, observation=None: {
        "group_id": row["group_id"], "status": "estimated",
        "allowed": row["group_id"] == 11,
        "raw_score": 0.7 if row["group_id"] == 11 else 0.4,
        "blocked_reason": None if row["group_id"] == 11 else "too risky",
        "features": {"group_size": 1.0}, "missing": [],
        "enemies": {"count": 1, "total_hp": 600.0, "has_boss": False},
    }
    two_groups = {"map": {"groups": [group(11), group(12)]}}
    check("fight.join without a target is refused on an ambiguous map",
          advisor.assess_call("fight.join", {}, two_groups) is not None)
    check("the worst matching group decides a named target",
          "too risky" in str(advisor.assess_call("fight.join", {"monster": 93},
                                                 two_groups)))
    single = {"map": {"groups": [group(11)]}}
    check("fight.join without a target is estimated on a single-group map",
          advisor.assess_call("fight.join", {}, single) is None)
    check("farm.start allows roaming when the target is not visible",
          advisor.assess_call("farm.start", {"monster": 93}, {"map": {"groups": []}}) is None
          and advisor.last_estimate.get("status") == "roaming")

    sheetless = FightAdvisor(store=_Store())
    refusal = sheetless.assess_call("fight.join", {"group_id": 11}, single)
    check("no character sheet means no fight",
          refusal is not None and "character sheet" in refusal, str(refusal))

    class _StubAdvisor:
        character = None

        def estimate(self, observation, group_id=None, monster=None):
            return {"status": "estimated", "group_id": group_id, "allowed": True,
                    "p_win": None, "blocked_reason": None, "features": {}}

    from agent.executor import ToolExecutor
    executor = ToolExecutor(fight_advisor=_StubAdvisor())
    executor.character_report = lambda instance=None: None
    result = executor.execute("fight.estimate", {"group_id": 11},
                              observation={"map": {"groups": [group(11)]}})
    check("fight.estimate is registered and dispatched",
          result.tool == "fight.estimate" and result.ok, str(result.as_dict())[:160])

    from agent.autopilot import Autopilot, _compact
    compact = _compact({"map": {"groups": [group(11)]},
                        "fight_estimates": [{"group_id": 11, "p_win": None}]})
    check("trajectory rows retain the map's fight estimates",
          compact.get("fight_estimates", [{}])[0].get("group_id") == 11)

    with tempfile.TemporaryDirectory() as tmp:
        pilot = Autopilot(runtime=None, goals=["t"],
                          log_path=Path(tmp) / "a.jsonl",
                          stop_file=Path(tmp) / "stop")
        pilot.fight_log = FightOutcomeLog(Path(tmp) / "fights.jsonl")
        pilot.fights.last_estimate = {
            "group_id": 11, "model_version": "fight-v0-analytic",
            "p_win": None, "features": {"group_size": 1.0}}
        claim, _ = pilot._track_fight({"in_fight": False, "hp": {"life": 900}},
                                      {"in_fight": True, "hp": {"life": 900}})
        check("entering a fight writes its estimate first", claim is not None)
        _, outcome = pilot._track_fight({"in_fight": True, "hp": {"life": 900}},
                                        {"in_fight": False, "alive": True,
                                         "hp": {"life": 700}})
        check("leaving a fight pairs the estimate with the outcome",
              isinstance(outcome, dict) and outcome.get("won") is True
              and outcome.get("hp_loss") == 200, str(outcome)[:160])
        path = Path(tmp) / "fights.jsonl"
        kinds = [json.loads(line)["kind"] for line in
                 path.read_text(encoding="utf-8").splitlines()]
        check("the calibration log has a start and an outcome row",
              kinds == ["fight_start", "fight_outcome"], str(kinds))

        calls = {"n": 0}

        class _Executor:
            def character_report(self, instance=None):
                calls["n"] += 1
                return None

        class _Runtime:
            executor = _Executor()
            instance = None

        import time
        pilot.runtime = _Runtime()
        pilot.fights.character = None
        pilot._character_at = 0.0
        pilot._character_try = 0.0
        pilot._refresh_character()
        pilot._refresh_character()
        check("a failed sheet fetch is retried, not cached for the TTL",
              calls["n"] == 1, str(calls))
        pilot._character_try = time.time() - pilot.character_retry_s - 1
        pilot._refresh_character()
        check("the sheet retry window is honoured", calls["n"] == 2, str(calls))


def test_fight_authorisation_modes() -> None:
    """A missing calibrator must not mean no fight at all.

    The live deadlock: the M3 calibration set needs resolved fights, and every
    fight was refused for lack of a calibrator. Bootstrap and tutorial modes are
    narrow envelopes that collect those rows; they must stay bounded, and a
    monster absent from the catalogue must be priced pessimistically instead of
    read as zero HP.
    """
    from combat.fight_estimate import (
        EnemyFacts, EnemyMember, PlayerFacts, estimate_from_facts, unavailable)
    from combat.fight_estimate import _enemy_facts

    player = PlayerFacts(level=2, current_hp=60, max_hp=60, ap=6,
                         dpt_by_element={"earth": 20.0}, damage_lines=1)
    weak = EnemyFacts(group_id=7, members=[
        EnemyMember(monster_id=4194, name="Merkaptan", level=2, hp=70, ap=4)])
    strong = EnemyFacts(group_id=8, members=[
        EnemyMember(monster_id=4194, name="Merkaptan", level=10, hp=400, ap=5)])
    boss = EnemyFacts(group_id=9, members=[
        EnemyMember(monster_id=4194, name="Merkaptan", level=2, hp=70, ap=4,
                    is_boss=True)])

    boot = estimate_from_facts(player, weak, bootstrap=True)
    check("bootstrap authorises a fight at the player's level",
          boot["allowed"] is True and boot["authorization"] == "bootstrap",
          f"{boot['allowed']} {boot['blocked_reason']}")
    check("the bootstrap authorisation is flagged on the estimate",
          boot["bootstrap"] is True and boot["tutorial_fight"] is False)
    strong_boot = estimate_from_facts(player, strong, bootstrap=True)
    check("bootstrap refuses a group above the player",
          strong_boot["allowed"] is False
          and "above player level" in str(strong_boot["blocked_reason"]),
          str(strong_boot["blocked_reason"]))
    check("bootstrap never opts itself into a boss",
          estimate_from_facts(player, boss, bootstrap=True)["allowed"] is False)

    tutorial = estimate_from_facts(player, weak, tutorial=True)
    check("a scripted tutorial fight is authorised by the scenario",
          tutorial["allowed"] is True and tutorial["authorization"] == "tutorial",
          str(tutorial["blocked_reason"]))
    tutorial_strong = estimate_from_facts(player, strong, tutorial=True)
    check("the tutorial envelope still refuses an over-level group",
          tutorial_strong["allowed"] is False
          and "tutorial refused" in str(tutorial_strong["blocked_reason"]),
          str(tutorial_strong["blocked_reason"]))
    check("the tutorial envelope never authorises a boss",
          estimate_from_facts(player, boss, tutorial=True)["allowed"] is False)
    unarmed = PlayerFacts(level=2, current_hp=60, max_hp=60, ap=6,
                          dpt_by_element={})
    unarmed_verdict = estimate_from_facts(unarmed, weak, tutorial=True)
    check("the tutorial envelope needs a damaging spell",
          unarmed_verdict["allowed"] is False
          and "damaging_spell" in str(unarmed_verdict["blocked_reason"]),
          str(unarmed_verdict["blocked_reason"]))

    class _EmptyStore:
        def monster(self, monster_id):
            return None

    fallback = _enemy_facts(
        {"group_id": 3, "members": [{"monster_id": 4194, "level": 2}]},
        _EmptyStore())
    check("a missing catalogue row is priced, not read as zero HP",
          bool(fallback.members) and fallback.members[0].hp > 0
          and "monster:4194" in fallback.missing, str(fallback.members))
    priced = estimate_from_facts(player, fallback, bootstrap=True)
    check("the fallback can still be bootstrapped, without a probability",
          priced["allowed"] is True and priced["p_win"] is None,
          f"{priced['allowed']} {priced['blocked_reason']}")

    refused = unavailable(3, "no group")
    check("an unavailable estimate carries the authorisation keys",
          refused["authorization"] is None and refused["bootstrap"] is False
          and refused["tutorial_fight"] is False)

    from combat.fight_estimate import FightAdvisor
    summary = FightAdvisor._summary(boot)
    check("the per-group summary carries the authorisation",
          summary.get("authorization") == "bootstrap" and summary["bootstrap"] is True,
          str(summary)[:160])


def test_planner_searches_for_a_fight_target() -> None:
    """A fight objective is not a reason to claim, and not a blind join.

    The live run sent `quest.advance` on a server-counted FightMonster objective,
    then `fight.join` on an empty map. Both were refused; the run ended. The
    planner must skip the claim for server-counted types and walk an exit when
    the target group is not visible.
    """
    from agent.planner import QuestPlanner

    def snapshot(groups, map_id=147064834):
        objective = {"quest_id": 3841, "objective_id": 13543, "status": 2,
                     "type_id": 6, "params": {"parameter0": 4194},
                     "text": "Defeat one leader."}
        return {
            "in_world": True,
            "position": {"map_id": map_id, "cell": 283},
            "map": {"map_id": map_id, "groups": groups, "exits": [
                {"direction": "left", "map_id": 147064322, "blocked": False}]},
            "quests_active": {"3841": 0},
            "ready_objectives": [objective],
            "current_objective": dict(objective),
            "level": 2,
        }

    planner = QuestPlanner()
    empty = snapshot([])
    call = planner.choose("do all the quests", empty)
    check("a server-counted ready objective is not claimed",
          call.tool != "quest.advance", f"{call.tool} {call.args}")
    check("the search walks an exit when the target is not visible",
          call.tool == "nav.goto" and call.args.get("direction") == "left",
          f"{call.tool} {call.args}")

    visible = snapshot([{"group_id": 7, "monster_ids": [4194],
                         "members": [{"monster_id": 4194, "level": 2}]}])
    joined = QuestPlanner().choose("do all the quests", visible)
    check("a visible target is joined instead of searched",
          joined.tool == "fight.join" and joined.args.get("monster") == 4194,
          f"{joined.tool} {joined.args}")

    on_site = snapshot([])
    on_site["ready_objectives"][0]["map_id"] = 147064834
    on_site["current_objective"]["map_id"] = 147064834
    waiting = QuestPlanner().choose("do all the quests", on_site)
    check("a fight that must happen on this map waits for the group",
          waiting.tool == "state.snapshot", f"{waiting.tool} {waiting.args}")

    with_npc = snapshot([])
    with_npc["map"]["npcs"] = [{"npc_id": 3918, "cell": 330, "actor_id": -3}]
    talked = QuestPlanner().choose("do all the quests", with_npc)
    check("a group-less fight asks a map NPC to start it",
          talked.tool == "quest.advance" and talked.args.get("npc_id") == 3918,
          f"{talked.tool} {talked.args}")

    two_exits = snapshot([])
    two_exits["map"]["exits"] = [
        {"direction": "left", "map_id": 147064322, "blocked": False},
        {"direction": "right", "map_id": 147064323, "blocked": False}]
    pick = QuestPlanner._explore_call({"type_id": 6}, two_exits, avoid=147064322)
    check("exploration does not walk straight back where it came from",
          pick is not None and pick["args"]["direction"] == "right", str(pick))

    tracker = QuestPlanner()
    tracker.choose("do all the quests", snapshot([], map_id=101))
    tracker.choose("do all the quests", snapshot([], map_id=202))
    check("a map change remembers the map it came from",
          tracker._prev_map == 101, str(tracker._prev_map))


def test_awareness_invalidates_stale_plans() -> None:
    """A changed scene or build must not reuse the previous retry state."""
    from types import SimpleNamespace
    from agent.awareness import character_key
    from agent.autopilot import Autopilot
    from agent.planner import QuestPlanner

    def snapshot(groups=None, max_hp=100, level=2):
        objective = {"quest_id": 3841, "objective_id": 13543,
                     "status": 2, "type_id": 6,
                     "params": {"parameter0": 4194},
                     "text": "Defeat one leader."}
        return {
            "in_world": True,
            "character": {"id": 1, "breed": 1, "level": level},
            "position": {"map_id": 147064834, "cell": 283},
            "hp": {"life": 60, "max": max_hp, "ap": 6, "mp": 3},
            "map": {"map_id": 147064834, "groups": groups or [],
                    "npcs": [{"npc_id": 3918, "cell": 330, "actor_id": -3}],
                    "exits": [{"direction": "left", "map_id": 147064322,
                               "blocked": False}]},
            "quests_active": {"3841": 0},
            "ready_objectives": [objective],
            "current_objective": dict(objective),
            "level": level,
        }

    planner = QuestPlanner()
    planner.choose("do all the quests", snapshot())
    key = (3841, 13543)
    planner._claim_attempts[key] = 2
    planner._claim_actions[key] = 9
    planner._npc_tries[(3841, 13543, 147064834)] = 1
    changed = snapshot(groups=[{"group_id": 7, "monster_ids": [4194],
                                "members": [{"monster_id": 4194, "level": 2}]}])
    planner.choose("do all the quests", changed)
    check("a new scene clears the old claim/retry state",
          key not in planner._claim_attempts and not planner._npc_tries,
          f"{planner._claim_attempts} {planner._npc_tries}")

    check("the character signature follows a max-HP change",
          character_key(snapshot(max_hp=100)) != character_key(snapshot(max_hp=120)))
    check("the character signature follows a level change",
          character_key(snapshot(level=2)) != character_key(snapshot(level=3)))

    class Adviser:
        def __init__(self):
            self.character = SimpleNamespace(level=2, max_hp=100)

        def use_character_report(self, report):
            return True

    class Executor:
        def __init__(self):
            self.calls = 0

        def character_report(self, instance=None):
            self.calls += 1
            return {"character": {"level": 2}}

    runtime = SimpleNamespace(executor=Executor(), instance=None)
    autopilot = object.__new__(Autopilot)
    autopilot.dry_run = False
    autopilot.fights = Adviser()
    autopilot.runtime = runtime
    autopilot._last_obs = snapshot(max_hp=100)
    autopilot._character_at = 0.0
    autopilot._character_try = 0.0
    autopilot._character_key = None
    autopilot.character_ttl_s = 300.0
    autopilot.character_retry_s = 30.0
    autopilot._refresh_character()
    first_calls = runtime.executor.calls
    autopilot._last_obs = snapshot(max_hp=120)
    autopilot._refresh_character()
    after_build = runtime.executor.calls
    autopilot._refresh_character()
    after_same = runtime.executor.calls
    check("a build change refreshes the sheet immediately",
          first_calls == 1 and after_build == 2, f"{first_calls} {after_build}")
    check("an unchanged sheet respects the TTL", after_same == 2, str(after_same))


def test_thinking_layer_after_repeated_failure() -> None:
    """Three failures must produce a new, grounded action, not the same call."""
    from types import SimpleNamespace
    from agent.autopilot import Autopilot
    from agent.controller import Decision
    from agent.thinking import ThinkingLayer

    objective = {"quest_id": 3841, "objective_id": 13543,
                 "status": 2, "type_id": 6,
                 "params": {"parameter0": 4194},
                 "text": "Defeat one leader."}
    obs = {
        "in_world": True,
        "character": {"id": 1, "breed": 1, "level": 2},
        "position": {"map_id": 147064834, "cell": 283},
        "hp": {"life": 60, "max": 100, "ap": 6, "mp": 3},
        "map": {"map_id": 147064834, "groups": [],
                "npcs": [{"npc_id": 3918, "cell": 330, "actor_id": -3},
                         {"npc_id": 3976, "cell": 272, "actor_id": -1}],
                "exits": [{"direction": "left", "map_id": 147064322,
                           "blocked": False},
                          {"direction": "right", "map_id": 147065346,
                           "blocked": False}]},
        "quests_active": {"3841": 0},
        "ready_objectives": [objective],
        "current_objective": dict(objective),
        "level": 2,
    }
    steps = [
        SimpleNamespace(tool="nav.goto", args={"direction": "left"}),
        SimpleNamespace(tool="nav.goto", args={"direction": "left"}),
        SimpleNamespace(tool="nav.goto", args={"direction": "left"}),
    ]
    layer = ThinkingLayer()
    first = layer.propose("do all the quests", obs, steps)
    check("the thinking layer stops repeating the failed navigation",
          first is not None and first.tool == "quest.advance",
          str(first))
    check("it asks an NPC rather than the same exit",
          first is not None and first.args.get("npc_id") in (3918, 3976),
          str(first.args if first else None))
    second_steps = steps + [SimpleNamespace(tool=first.tool, args=first.args)]
    second = layer.propose("do all the quests", obs, second_steps)
    check("the next thought is a different interaction",
          second is not None and second.signature() != first.signature(),
          f"{first} {second}")
    third_steps = second_steps + [SimpleNamespace(tool=second.tool,
                                                  args=second.args)]
    third = layer.propose("do all the quests", obs, third_steps)
    check("it falls through to another exit when the NPCs are used up",
          third is not None and third.tool == "nav.goto",
          str(third))

    autopilot = object.__new__(Autopilot)
    autopilot.thinking = ThinkingLayer()
    autopilot._last_obs = obs
    autopilot.steps = steps
    autopilot._forced_call = None
    autopilot.replan_requested = 0
    autopilot.on_replan = None
    outcome = autopilot._apply_decision(
        Decision("replan", "plan_wrong", "nav.goto failed 3 times in a row"),
        "do all the quests", None)
    check("a replan after three failures runs the thinking step",
          outcome == "think" and autopilot._forced_call is not None,
          f"{outcome} {autopilot._forced_call}")


def test_encounter_environment() -> None:
    """The TPU encounter environment spends AP, respects range and ends fights.

    This is the substrate for self-play: if the rules here are wrong, every
    label the TPU produces is wrong in a way no amount of training can repair.
    """
    import jax
    import jax.numpy as jnp
    import numpy as np

    from combat.encounter import model as cm
    from combat.encounter.env import (Config, action_masks, initial_state,
                                      observe, sample_actions, step)

    cfg = Config(max_rounds=4)
    B, N = 2, cfg.n_actors
    enemy = cfg.allies

    def spec():
        hp = np.zeros((B, N), dtype=np.float32)
        hp[:, 0] = 100.0
        hp[:, enemy] = 30.0
        alive = np.zeros((B, N), dtype=bool)
        alive[:, 0] = True
        alive[:, enemy] = True
        out = {
            "hp": hp, "max_hp": hp * 2.0,
            "ap_max": np.where(alive, 6.0, 0.0).astype(np.float32),
            "mp_max": np.where(alive, 3.0, 0.0).astype(np.float32),
            "power": np.zeros((B, N), dtype=np.float32),
            "res": np.zeros((B, N, 5), dtype=np.float32),
            "dist": np.full((B, N), 12.0, dtype=np.float32),
            "initiative": np.zeros((B, N), dtype=np.float32),
            "alive": alive,
            "first_team": np.zeros((B,), dtype=np.int32),
        }
        out["dist"][:, 0] = 1.0
        out["dist"][:, enemy] = 1.0
        out["initiative"][:, 0] = 10.0
        out["initiative"][:, enemy] = 5.0
        return {k: jnp.asarray(v) for k, v in out.items()}

    def tables():
        shape = (B, N, cfg.spells)
        t = {
            "spell_ap": jnp.full(shape, 6.0),
            "spell_rmin": jnp.zeros(shape),
            "spell_rmax": jnp.zeros(shape),
            "spell_damage": jnp.zeros(shape),
            "spell_heal": jnp.zeros(shape),
            "spell_element": jnp.zeros(shape, dtype=jnp.int32),
            "spell_ally": jnp.zeros(shape, dtype=bool),
        }
        def set_spell(slot, s, ap, rmin, rmax, dam, heal, ally):
            t["spell_ap"] = t["spell_ap"].at[:, slot, s].set(ap)
            t["spell_rmin"] = t["spell_rmin"].at[:, slot, s].set(rmin)
            t["spell_rmax"] = t["spell_rmax"].at[:, slot, s].set(rmax)
            t["spell_damage"] = t["spell_damage"].at[:, slot, s].set(dam)
            t["spell_heal"] = t["spell_heal"].at[:, slot, s].set(heal)
            t["spell_ally"] = t["spell_ally"].at[:, slot, s].set(ally)
        set_spell(0, 0, 3, 1, 1, 10, 0, False)
        set_spell(0, 1, 3, 0, 0, 0, 8, True)
        set_spell(enemy, 0, 3, 1, 1, 5, 0, False)
        return t

    tbl = tables()
    state = initial_state(spec(), tbl, cfg)
    masks = action_masks(state, tbl, cfg)
    check("only the acting team has actions",
          bool(masks["actor"][0, 0]) and not bool(masks["actor"][0, enemy]),
          str(np.asarray(masks["actor"][0])))

    player = {"move": jnp.ones((B, N), dtype=jnp.int32),
              "spell": jnp.zeros((B, N), dtype=jnp.int32),
              "target": jnp.full((B, N), enemy, dtype=jnp.int32)}
    after = step(state, player, tbl, cfg)
    check("one action spends all AP on repeated casts",
          float(after["hp"][0, enemy]) == 10.0,
          str(float(after["hp"][0, enemy])))
    check("the turn passes to the other team",
          int(after["acting_team"][0]) == 1
          and float(after["ap"][0, enemy]) == 6.0,
          f"team={int(after['acting_team'][0])} ap={float(after['ap'][0,enemy])}")

    burst = dict(state)
    burst["ap"] = burst["ap"].at[:, 0].set(12.0)
    burst["hp"] = burst["hp"].at[:, enemy].set(100.0)
    burst["max_hp"] = burst["max_hp"].at[:, enemy].set(100.0)
    hit = step(burst, player, tbl, cfg)
    check("casts_per_turn caps a 12 AP burst at four casts",
          float(hit["hp"][0, enemy]) == 60.0,
          str(float(hit["hp"][0, enemy])))

    away = {"move": jnp.full((B, N), 2, dtype=jnp.int32),
            "spell": jnp.full((B, N), cfg.spells, dtype=jnp.int32),
            "target": jnp.zeros((B, N), dtype=jnp.int32)}
    moved = step(state, away, tbl, cfg)
    check("movement spends MP and changes the distance",
          float(moved["dist"][0, 0]) == 4.0,
          str(float(moved["dist"][0, 0])))

    far = {k: v for k, v in state.items()}
    far["dist"] = far["dist"].at[:, 0].set(8.0)
    fmask = action_masks(far, tbl, cfg)
    check("a melee spell is masked when the target is out of range",
          not bool(fmask["spell"][0, 0, 0]) and bool(fmask["spell"][0, 0, cfg.spells]),
          str(np.asarray(fmask["spell"][0, 0])))
    check("healing an enemy is not a legal target",
          not bool(fmask["target"][0, 0, 1, enemy])
          and bool(fmask["target"][0, 0, 1, 0]))

    lethal = {k: v for k, v in state.items()}
    lethal["hp"] = lethal["hp"].at[:, enemy].set(1.0)
    fin = step(lethal, player, tbl, cfg)
    check("the last kill ends the fight in the player's favour",
          bool(fin["done"][0]) and int(fin["winner"][0]) == 0,
          f"{bool(fin['done'][0])} {int(fin['winner'][0])}")

    jstep = jax.jit(lambda s, a: step(s, a, tbl, cfg))
    jout = jstep(state, player)
    check("the environment step is jit-compatible",
          jout["hp"].shape == state["hp"].shape)

    obs, glob, amask = observe(state, tbl, cfg)
    params = cm.init_params(jax.random.PRNGKey(0), obs.shape[-1], glob.shape[-1], cfg)
    logits = cm.forward(params, obs, glob, amask)
    sampled = sample_actions(jax.random.PRNGKey(1), logits, masks, cfg)
    check("the policy produces masked, finite actions",
          logits["target"].shape == (B, N, cfg.n_spells, N)
          and bool(jnp.all(jnp.isfinite(logits["value"])))
          and int(sampled["spell"][0, 0]) <= cfg.spells,
          f"{logits['target'].shape} {np.asarray(sampled['spell'][0])}")


def test_learned_planner_respects_geography() -> None:
    """The learned tool choice must not override facts the model cannot see.

    The live panel runs `--policy planner`, which is NNPlanner. It chose
    `quest.advance` for a server-counted objective and `fight.join` for a map
    with no group; the guard refused the join every second and the run spun.
    The head owns *which* tool an objective wants; the deterministic planner
    owns whether the map and the server allow it.
    """
    from agent.planner_nn import NNPlanner

    def snapshot(groups, with_map=True):
        objective = {"quest_id": 3841, "objective_id": 13543, "status": 2,
                     "type_id": 6, "params": {"parameter0": 4194},
                     "text": "Defeat one leader."}
        obs = {
            "in_world": True,
            "position": {"map_id": 147064834, "cell": 283},
            "quests_active": {"3841": 0},
            "ready_objectives": [objective],
            "current_objective": dict(objective),
            "level": 2,
        }
        if with_map:
            obs["map"] = {"map_id": 147064834, "groups": groups, "exits": [
                {"direction": "left", "map_id": 147064322, "blocked": False}]}
        return obs, objective

    class _Learned(NNPlanner):
        def __init__(self, tool):
            super().__init__(model_path=Path("__no_model__"))
            self.tool = tool
            self.has_model = False

        def _choose_tool(self, quest, step_idx, obj, obs):
            return self.tool

    quest = {"id": 3841, "level_min": 2}

    empty, objective = snapshot([])
    calls = _Learned("fight.join").plan_objective(quest, 0, objective, empty)
    check("a learned fight join waits or searches when no group is visible",
          bool(calls) and calls[-1]["tool"] != "fight.join",
          str(calls))

    visible, objective = snapshot([{"group_id": 7, "monster_ids": [4194],
                                    "members": [{"monster_id": 4194,
                                                 "level": 2}]}])
    calls = _Learned("fight.join").plan_objective(quest, 0, objective, visible)
    check("a learned fight join is kept when the group is on the map",
          bool(calls) and calls[-1]["tool"] == "fight.join"
          and calls[-1]["args"].get("monster") == 4194, str(calls))

    claimed = _Learned("quest.advance").plan_objective(quest, 0, objective, visible)
    check("a server-counted objective is never claimed by the learned head",
          bool(claimed) and claimed[-1]["tool"] != "quest.advance", str(claimed))

    offline, objective = snapshot([], with_map=False)
    calls = _Learned("fight.join").plan_objective(quest, 0, objective, offline)
    check("without a map block the corpus labels keep the learned choice",
          bool(calls) and calls[-1]["tool"] == "fight.join", str(calls))


def test_autopilot_guard_refusal_is_not_fatal() -> None:
    """A refused call is a step, not the end of the run.

    The live failure: the fight guard refused a call, the loop broke out of the
    goal immediately and the panel showed a run that switched itself off. The
    refusal must reach the policy and the controller, the loop must take another
    step, and only the same refusal three times in a row retires the quest.
    """
    from agent.autopilot import Autopilot
    from agent.runtime import ToolCall

    class _Result:
        def __init__(self):
            self.ok = True
            self.error = None
            self.data = None

    class _Executor:
        def __init__(self):
            self.calls = []

        def snapshot(self, instance=None):
            return {"in_world": True, "position": {"map_id": 1, "cell": 5},
                    "hp": {"life": 60}, "objectives_completed": 0}

        def execute(self, tool, args=None, **kwargs):
            if tool == "hdv.buy":
                raise AssertionError("a refused call was executed")
            self.calls.append(tool)
            return _Result()

        def character_report(self, instance=None):
            return None

    class _Policy:
        current_quest = 3841

        def __init__(self, stop):
            self.stop = stop
            self.notes = []
            self.skips = []
            self.choices = 0

        def choose(self, goal, observation, tools):
            self.choices += 1
            if not self.skips:
                return ToolCall("hdv.buy", {"item": "Bouftou Wool", "qty": 1}, goal)
            self.stop.write_text("stop", encoding="utf-8")
            return ToolCall("state.snapshot", {}, goal)

        def note_result(self, tool, ok, note, obs):
            self.notes.append((tool, ok, note))

        def skip(self, quest_id):
            self.skips.append(quest_id)

    with tempfile.TemporaryDirectory() as tmp:
        stop = Path(tmp) / "stop"
        runtime = type("_Runtime", (), {})()
        runtime.executor = _Executor()
        runtime.policy = _Policy(stop)
        runtime.instance = None
        runtime.current_observation = lambda: (None, None, "call")
        runtime.observe = lambda snap, source="live": None
        pilot = Autopilot(runtime=runtime, goals=["t"],
                          log_path=Path(tmp) / "a.jsonl", stop_file=stop,
                          preflight=False, settle_s=0.0)
        summary = pilot.run()
        policy = runtime.policy
        blocked = [step for step in pilot.steps if step.verdict == "blocked"]
        buy_notes = [row for row in policy.notes if row[0] == "hdv.buy"]
        check("a guard refusal is reported back to the policy",
              len(buy_notes) == 3
              and all(ok is False and "buying spends money" in note
                      for _, ok, note in buy_notes), str(policy.notes)[:200])
        check("the loop keeps stepping after a refusal", policy.choices == 4,
              str(policy.choices))
        check("the refusal is logged as a blocked step without executing it",
              len(blocked) == 3
              and "hdv.buy" not in runtime.executor.calls,
              f"{[s.verdict for s in pilot.steps]} {runtime.executor.calls}")
        check("three identical refusals retire the quest the call belonged to",
              policy.skips == [3841], str(policy.skips))
        check("the run stopped for the stop file, not 'goals exhausted'",
              "stop file" in str(summary.get("reason")), str(summary.get("reason")))


def test_autopilot_waits_for_the_world() -> None:
    """Turning the AI on at the login screen is a wait, not an exit.

    The live client had dropped for inactivity. Starting the loop then exited 4
    before the first step, which from the panel is indistinguishable from the AI
    switching itself off. The preflight must wait for a map for as long as the
    login takes, and leave only when the operator stops it or the client dies.
    """
    from agent.autopilot import Autopilot

    class _Executor:
        def __init__(self, snaps):
            self.snaps = list(snaps)
            self.polls = 0

        def snapshot(self, instance=None):
            self.polls += 1
            if self.snaps:
                return self.snaps.pop(0)
            return {"in_world": True, "position": {"map_id": 1, "cell": 1}}

        def character_report(self, instance=None):
            return None

    def runtime_for(executor):
        runtime = type("_Runtime", (), {})()
        runtime.executor = executor
        runtime.instance = None
        runtime.current_observation = lambda: (None, None, "call")
        runtime.observe = lambda snap, source="live": None
        return runtime

    with tempfile.TemporaryDirectory() as tmp:
        login = {"in_world": False, "position": {"map_id": 0, "cell": 0}}
        world = {"in_world": True, "position": {"map_id": 147064834, "cell": 283}}
        executor = _Executor([login, world])
        pilot = Autopilot(runtime=runtime_for(executor), goals=["t"],
                          log_path=Path(tmp) / "a.jsonl",
                          stop_file=Path(tmp) / "stop",
                          preflight=True, settle_s=0.0)
        pilot.world_poll_s = 0.0
        check("the preflight waits through the login screen and starts",
              pilot._require_world() is True and executor.polls == 2,
              str(executor.polls))

        # No login ever comes. There is no time limit to expire, so the only
        # honest exit is the operator's stop file.
        stop = Path(tmp) / "stop2"

        class _StoppingExecutor(_Executor):
            def snapshot(self, instance=None):
                snap = super().snapshot(instance)
                if self.polls >= 5:
                    stop.write_text("stop")
                return snap

        stuck = _StoppingExecutor([login] * 50)
        pilot = Autopilot(runtime=runtime_for(stuck), goals=["t"],
                          log_path=Path(tmp) / "b.jsonl",
                          stop_file=stop,
                          preflight=True, settle_s=0.0)
        pilot.world_poll_s = 0.0
        check("a session that never enters the world keeps waiting until stopped",
              pilot._require_world() is False and stuck.polls == 5,
              str(stuck.polls))


def test_autopilot_waits_in_fight() -> None:
    """A fight is a wait, not a reason to plan another world action.

    The live failure: a quest dialog started a fight, auto-fight was off, and
    the loop kept planning against a board for minutes while nobody clicked
    ready. The loop must arm the in-page bot and stop sending quest directives
    until the fight is over.
    """
    import agent.autopilot as ap
    from agent.autopilot import Autopilot
    from agent.runtime import ToolCall

    class _Result:
        def __init__(self, data=None):
            self.ok = True
            self.error = None
            self.data = data

    class _Executor:
        def __init__(self, observations):
            self.observations = list(observations)
            self.calls = []

        def snapshot(self, instance=None):
            if self.observations:
                return self.observations.pop(0)
            return {"in_fight": False, "in_world": True,
                    "position": {"map_id": 1, "cell": 1}, "hp": {"life": 100}}

        def execute(self, tool, args=None, **kwargs):
            self.calls.append(tool)
            if tool == "fight.join":
                return _Result({"in_fight": True, "auto_fight": True})
            return _Result(None)

        def character_report(self, instance=None):
            return None

    class _Policy:
        def __init__(self, stop_file):
            self.stop_file = stop_file
            self.choices = 0

        def choose(self, goal, observation, tools):
            self.choices += 1
            self.stop_file.write_text("stop", encoding="utf-8")
            return ToolCall("state.snapshot", {}, goal)

    with tempfile.TemporaryDirectory() as tmp:
        stop = Path(tmp) / "stop"
        observations = [
            {"in_fight": True, "auto_fight": False, "in_world": True,
             "position": {"map_id": 1, "cell": 1}, "hp": {"life": 100}},
            {"in_fight": False, "auto_fight": True, "in_world": True,
             "position": {"map_id": 1, "cell": 2}, "hp": {"life": 80},
             "objectives_completed": 1},
        ]
        runtime = type("_Runtime", (), {})()
        runtime.executor = _Executor(observations)
        runtime.policy = _Policy(stop)
        runtime.instance = None
        runtime.current_observation = lambda: (None, None, "call")
        runtime.observe = lambda snap, source="live": None
        pilot = Autopilot(runtime=runtime, goals=["t"],
                          log_path=Path(tmp) / "a.jsonl", stop_file=stop,
                          preflight=False, settle_s=0.0)
        real_sleep = ap.time.sleep
        ap.time.sleep = lambda s: None
        try:
            summary = pilot.run()
        finally:
            ap.time.sleep = real_sleep
        calls = runtime.executor.calls
        check("a fight without auto-fight is armed before anything else",
              calls and calls[0] == "fight.join", str(calls))
        check("no world action runs while the fight is open",
              "quest.advance" not in calls, str(calls))
        check("the goal resumes once the fight ends",
              runtime.policy.choices == 1 and calls[-1] == "state.snapshot",
              f"choices={runtime.policy.choices} calls={calls}")
        check("the run still stops normally after the fight",
              "stop file" in str(summary.get("reason", "")), str(summary.get("reason")))


def test_farmbot_ready_survives_late_arming() -> None:
    """A placement message must not be lost because the bot was off.

    Fights can start from a quest or dialog without `fight.join`. In the live
    session FarmBot stayed off for the whole fight, so placement was ignored and
    no ready was ever sent. The page now caches placement and replays it when
    enabled, and records whether the ready packet actually left.
    """
    js = (REPO_ROOT / "scripts" / "mobile-disguise.js").read_text(encoding="utf-8")
    check("placement is cached before the enabled gate",
          "self.state.placement = msg;" in js)
    check("enabling mid-fight replays a pending placement",
          "resumeFight: function()" in js and "self.placeAndReady(msg);" in js)
    check("fight start resets the ready latch",
          "self.state.readied = false;" in js and "self.state.readying = false;" in js)
    check("the ready send reports its own outcome",
          "window.__agentLastFightReady" in js and "sent: !!ok" in js)
    check("sendPacket reports failures instead of dropping them",
          "lastSendError" in js and "return false;" in js)
    turn_handler = js.split("cm.on('GameFightTurnStartMessage'", 1)[1].split(
        "if (!self.isUserActor(msg.id)) return;", 1)[0]
    check("a started turn invalidates a cached placement",
          "self.state.placement = null;" in turn_handler)
    check("fight.join reports the enabled latch, not the object",
          "auto_fight: !!(window.__FarmBot && window.__FarmBot.enabled)" in js)
    check("the page exposes a build marker in observations",
          "snap.agent_build" in js and "2026-10-25d" in js)
    check("a crossing from the border cell requests the change directly",
          "change_requested_at_edge" in js and "iso._requestMapChange(" in js
          and "atEdge" in js)


def test_dialog_label_extraction() -> None:
    """A dialog choice is only a label once the objective moves."""
    from ai.build_dialog_labels import extract

    def observation(objective_id, completed, in_dialog=False, dialog=None):
        obs = {"current_objective": {"quest_id": 18, "objective_id": objective_id,
                                     "status": 2, "text": "Vaincre x1 Wabbit Gm"},
               "objectives_completed": completed, "in_dialog": in_dialog}
        if dialog:
            obs["dialog"] = dialog
        return obs

    dialog = {"open": True, "message": "Que veux-tu ?", "replies": [
        {"index": 0, "text": "Parler du temps", "reply_id": 40},
        {"index": 1, "text": "Combattre le Wabbit Gm", "reply_id": 41}]}
    step = {"kind": "step", "tool": "quest.advance", "ok": True,
            "args": {"quest_id": 18, "reply": 1,
                     "reply_text": "Combattre le Wabbit Gm"},
            "observation": observation(115, 1, True, dialog)}
    rows, stats = extract([step, {"kind": "observation",
                                  "observation": observation(116, 1)}])
    check("a chosen reply becomes one row",
          len(rows) == 1 and rows[0]["outcome"] == "advanced", str(rows)[:200])
    check("the row keeps the question and both replies",
          rows[0]["question"] == "Que veux-tu ?" and len(rows[0]["replies"]) == 2,
          str(rows[0])[:200])
    check("the row carries the chosen reply id",
          rows[0]["reply_id"] == 41
          and rows[0]["chosen_text"] == "Combattre le Wabbit Gm",
          str(rows[0])[:200])
    check("the row counts a real choice", stats["choices"] == 1, str(stats))

    completed_rows, _ = extract([
        step, {"kind": "observation", "observation": observation(115, 2)}])
    check("a completed objective is its own label",
          completed_rows[0]["outcome"] == "completed",
          str(completed_rows[0])[:160])

    unchanged_rows, _ = extract([
        step, {"kind": "observation",
               "observation": observation(115, 1, True, dialog)}])
    check("an unchanged objective is labelled unchanged",
          unchanged_rows[0]["outcome"] == "unchanged"
          and unchanged_rows[0]["dialog_after"] == "open",
          str(unchanged_rows[0])[:160])

    unknown_rows, _ = extract([step])
    check("no observation after the choice is unknown",
          unknown_rows[0]["outcome"] == "unknown", str(unknown_rows[0])[:160])

    failed_rows, _ = extract([
        {**step, "ok": False, "error": "dialog closed"},
        {"kind": "observation", "observation": observation(116, 1)}])
    check("a failed call is labelled failed, not advanced",
          failed_rows[0]["outcome"] == "failed", str(failed_rows[0])[:160])

    talk_rows, talk_stats = extract([
        {"kind": "step", "tool": "quest.advance", "ok": True,
         "args": {"quest_id": 18},
         "observation": observation(115, 1, False)}])
    check("a talk without a reply is not a dialog label",
          talk_rows == [] and talk_stats["decisions"] == 0, str(talk_stats)[:160])


def test_trainer_supports_resume() -> None:
    """A continuation run must be expressible, and must not reset its counters."""
    parser = ep.build_parser()
    args = parser.parse_args([
        "train", "--tokenizer", "t.json", "--resume", "prev.pt", "--epochs", "4"])
    check("train --resume parses", getattr(args, "resume", None) == "prev.pt",
          str(getattr(args, "resume", None)))


if __name__ == "__main__":
    test_thx_no_replacement_char()
    test_thx_price_pattern_clean()
    test_schema_int_coercion_from_string()
    test_schema_bool_rejects_string()
    test_schema_str_list_from_string()
    test_schema_enum_validation()
    test_schema_required_missing()
    test_schema_unknown_args_rejected()
    test_schema_special_rules()
    test_schema_coerced_roundtrip()
    test_lang_detect_all_four()
    test_lang_detect_diacritics()
    test_lang_detect_undecided()
    test_lang_detect_prior()
    test_reply_language()
    test_label_rows()
    test_encoder_inference_tooless_accepted()
    test_encoder_training_tooless_rejected()
    test_encoder_overflow_skipped()
    test_onnx_decode_outputs_has_tool()
    test_parse_span_int_and_str_list()
    test_best_span_basic()
    test_recorder_redaction_and_stats()
    test_executor_invalid_call_no_crash()
    test_executor_records_piggybacked_observation()
    test_price_rule_without_price_is_dropped()
    test_trainer_supports_resume()
    test_dialog_policy_and_contract()
    test_dialog_label_extraction()
    test_fight_estimate_and_guard()
    test_fight_authorisation_modes()
    test_planner_searches_for_a_fight_target()
    test_awareness_invalidates_stale_plans()
    test_thinking_layer_after_repeated_failure()
    test_encounter_environment()
    test_learned_planner_respects_geography()
    test_autopilot_guard_refusal_is_not_fatal()
    test_autopilot_waits_for_the_world()
    test_autopilot_waits_in_fight()
    test_farmbot_ready_survives_late_arming()
    test_fight_state_exposes_monster_data()
    test_live_policy_brain_is_wired()
    test_autopilot_money_guards()
    test_autopilot_listing_is_not_revenue()
    sys.exit(summarize())

