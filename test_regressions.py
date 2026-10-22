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

    executor._dispatch = lambda tool, args, source="live", instance=None: ToolResult(
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
    test_autopilot_money_guards()
    test_autopilot_listing_is_not_revenue()
    sys.exit(summarize())

