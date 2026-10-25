"""Self-test for the agent package — no game, no network, no torch required.

Run from the ``dofus-network`` directory::

    python -m agent.selftest

Exit code 0 when every check passes. Network-dependent checks (knowledge
cache) degrade to SKIP, never to fail — the same convention as
``networkhealth.selftest``.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent import synthetic_tools, tool_dataset, tool_eval
from agent.executor import ToolExecutor
from agent.recorder import TrajectoryRecorder
from agent.runtime import AgentRuntime, RulePolicy
from agent.schema import TOOLS_BY_NAME, TOOL_SCHEMA, tool_names, validate_call
from ai import thx_policy as thx

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def skip(name: str, reason: str) -> None:
    RESULTS.append((name, True, f"skip: {reason}"))
    print(f"  [SKIP] {name} — {reason}")


def test_schema() -> None:
    print("\n== schema ==")
    names = tool_names()
    check("tool names unique", len(names) == len(set(names)))

    ok, error = validate_call("does.not.exist", {})
    check("unknown tool rejected", not ok and "unknown tool" in str(error))

    ok, _ = validate_call("quest.advance", {})
    check("missing required arg rejected", not ok)

    ok, args = validate_call("knowledge.search", {"query": "bouftou", "limit": 3})
    check("valid knowledge.search accepted", ok and args.get("limit") == 3)

    ok, args = validate_call("nav.goto", {"x": "12", "y": 8})
    check("string->int coercion", ok and args.get("x") == 12 and args.get("y") == 8)

    ok, _ = validate_call("nav.goto", {"x": 12})
    check("half coordinate pair rejected", not ok)

    ok, _ = validate_call("nav.goto", {})
    check("nav.goto without target rejected", not ok)

    ok, _ = validate_call("nav.goto", {"zaap": "astrub"})
    check("nav.goto zaap accepted", ok)

    ok, _ = validate_call("inv.sell", {"price_rule": "median_minus_1"})
    check("inv.sell without item/all rejected", not ok)

    ok, _ = validate_call("inv.sell", {"all": True, "price_rule": "median_minus_1"})
    check("inv.sell all accepted", ok)

    ok, _ = validate_call("inv.sell", {"item": "blé", "price_rule": "fixed"})
    check("inv.sell fixed without price rejected", not ok)

    ok, _ = validate_call("knowledge.search", {"query": "x", "bogus_arg": 1})
    check("unknown argument rejected", not ok)

    ok, args = validate_call("quest.list", {"area": "astrub", "level_min": 20})
    check("quest.list area accepted", ok and args.get("area") == "astrub" and args.get("level_min") == 20,
          str(args) if ok else str(args))

    check("schema languages 4", TOOL_SCHEMA["languages"] == ["en", "fr", "es", "de"])


def test_recorder(tmp: Path) -> None:
    print("\n== recorder ==")
    recorder = TrajectoryRecorder(root=tmp, instance="selftest", max_bytes=1024 * 1024)
    recorder.observation({"map": 1234, "cell": 300, "hp": 100}, goal="farm")
    record = recorder.step(
        tool="knowledge.search",
        args={"query": "bouftou"},
        ok=True,
        result={"results": [{"id": 31}]},
        latency_ms=1.5,
        goal="farm",
        observation={"account": "secret-account", "nested": {"password": "hunter2",
                                                             "url": "http://user:pw@host/"}},
    )
    lines = (tmp / "selftest.jsonl").read_text(encoding="utf-8").strip().splitlines()
    check("two records written", len(lines) == 2)

    parsed = [json.loads(line) for line in lines]
    check("step record round-trips", parsed[1]["tool"] == "knowledge.search")

    raw = json.dumps(record)
    check("account redacted", "secret-account" not in raw and "[redacted]" in raw)
    check("password redacted", "hunter2" not in raw)
    check("url credentials scrubbed", "user:pw@" not in raw)

    stats = recorder.stats()
    check("stats count steps", stats["steps_ok"] == 1 and stats["lines"] == 2)

    tail = recorder.tail(1)
    check("tail returns last record", len(tail) == 1 and tail[0]["kind"] == "step")

    rotating = TrajectoryRecorder(root=tmp, instance="rotate", max_bytes=1)
    rotating.step(tool="safe.stop", args={}, ok=True)
    rotating.step(tool="safe.stop", args={}, ok=False)
    rotated = list(tmp.glob("rotate.*.jsonl"))
    check("rotation produces an archive", len(rotated) >= 1)


def test_knowledge(executor: ToolExecutor) -> None:
    print("\n== knowledge (local cache) ==")
    monsters = REPO_ROOT / "data" / "cache" / "monsters_full.json"
    if not monsters.exists():
        skip("monster search", "monsters_full.json not cached yet")
        return
    result = executor.execute("knowledge.search", {"query": "bouftou", "kind": "monster", "limit": 3})
    check("knowledge.search runs", result.ok)
    hits = (result.data or {}).get("results", [])
    check("bouftou found in cache", len(hits) >= 1,
          f"top: {hits[0].get('name') if hits else 'none'}")


def test_quest_area() -> None:
    print("\n== quest area filter (local cache) ==")
    cache = REPO_ROOT / "data" / "cache"
    required = ("quests_full.json", "subareas_full.json", "areas_full.json")
    if not all((cache / name).exists() for name in required):
        skip("astrub quest filter", "quests/subareas/areas caches not synced yet")
        return
    executor = ToolExecutor(cache_dir=cache, timeout=0.3)

    result = executor.execute("quest.list", {"area": "astrub", "limit": 20})
    data = result.data or {}
    resolution = data.get("area") or {}
    check("astrub resolves to a known area",
          result.ok and resolution.get("resolved") is True,
          json.dumps({key: resolution.get(key) for key in ("areas", "subareas", "subarea_count")},
                     ensure_ascii=False))
    check("astrub quests found", int(data.get("count") or 0) > 0, f"count={data.get('count')}")

    result = executor.execute("quest.list", {"area": "Bonta", "level_min": 100, "limit": 5})
    quests = (result.data or {}).get("quests") or []
    check("level filter applies inside an area",
          result.ok and all(int(q.get("level_min") or 0) >= 100 for q in quests),
          f"{len(quests)} quests")

    result = executor.execute("quest.list", {"area": "zzznowhere", "limit": 5})
    data = result.data or {}
    resolution = data.get("area") or {}
    check("unknown area degrades gracefully",
          result.ok and resolution.get("resolved") is False and data.get("note"))


def test_executor_offline(tmp: Path) -> None:
    print("\n== executor (offline) ==")
    recorder = TrajectoryRecorder(root=tmp, instance="exec")
    executor = ToolExecutor(
        sentinel_url="http://127.0.0.1:1",
        directive_url="http://127.0.0.1:1/directive",
        cache_dir=REPO_ROOT / "data" / "cache",
        timeout=0.3,
        recorder=recorder,
    )

    result = executor.execute("inv.list", {"kind": "all"})
    check("directive tool fails cleanly when bridge is down",
          not result.ok and "not reachable" in (result.error or ""),
          result.error or "")

    result = executor.execute("nav.goto", {"x": 4, "y": -18})
    check("sentinel tool fails cleanly when sentinel is down",
          not result.ok and "sentinel" in (result.error or "").lower(),
          result.error or "")

    result = executor.execute("bogus.tool", {})
    check("invalid tool rejected at execution", not result.ok)

    stats = recorder.stats()
    check("executor records every attempt", stats["lines"] == 3 and stats["steps_failed"] == 3)


def test_runtime(tmp: Path) -> None:
    print("\n== runtime (rule policy) ==")
    policy = RulePolicy()

    call = policy.choose("stop everything now")
    check("stop -> safe.stop", call is not None and call.tool == "safe.stop")

    call = policy.choose("va en [12, 8]")
    check("coords -> nav.goto", call is not None and call.tool == "nav.goto"
          and call.args.get("x") == 12 and call.args.get("y") == 8,
          f"{call.tool} {call.args}" if call else "no call")

    call = policy.choose("plz sell all the items i have in inventory for currentmarket price -1")
    check("sell all -> inv.sell median_minus_1",
          call is not None and call.tool == "inv.sell"
          and call.args.get("all") is True
          and call.args.get("price_rule") == "median_minus_1",
          f"{call.tool} {call.args}" if call else "no call")

    call = policy.choose("do the quests under astrub")
    check("quests -> quest.list with area",
          call is not None and call.tool == "quest.list" and call.args.get("area") == "astrub",
          f"{call.tool} {call.args}" if call else "no call")

    call = policy.choose("fais les quêtes à Astrub")
    check("french area preposition captured",
          call is not None and call.tool == "quest.list" and call.args.get("area") == "Astrub",
          f"{call.tool} {call.args}" if call else "no call")

    call = policy.choose("misiones en Astrub")
    check("spanish area preposition captured",
          call is not None and call.tool == "quest.list" and call.args.get("area") == "Astrub",
          f"{call.tool} {call.args}" if call else "no call")

    call = policy.choose("Quests im Astrub")
    check("german area preposition captured",
          call is not None and call.tool == "quest.list" and call.args.get("area") == "Astrub",
          f"{call.tool} {call.args}" if call else "no call")

    call = policy.choose("farm kanigs until i have 200 loot")
    check("farm -> farm.start", call is not None and call.tool == "farm.start",
          f"{call.tool} {call.args}" if call else "no call")

    call = policy.choose("follow me")
    check("follow -> nav.follow", call is not None and call.tool == "nav.follow")

    call = policy.choose("blorb wibbity zzz")
    check("unknown goal left unresolved", call is None)

    recorder = TrajectoryRecorder(root=tmp, instance="runtime")
    runtime = AgentRuntime(
        executor=ToolExecutor(
            sentinel_url="http://127.0.0.1:1",
            directive_url="http://127.0.0.1:1/directive",
            timeout=0.3,
            recorder=recorder,
        ),
        recorder=recorder,
        policy=policy,
    )
    result = runtime.handle_goal("stop everything now")
    check("runtime executes a resolved goal", not result.ok or result.tool == "safe.stop")
    result = runtime.handle_goal("blorb wibbity zzz")
    check("runtime logs unresolved goals", not result.ok and "unresolved" in (result.error or ""))
    stats = recorder.stats()
    check("runtime trajectory captured", stats["lines"] >= 2, json.dumps(stats["tools"]))

    # -- the observation stream ------------------------------------------------
    # Every one of the 2,759 recorded steps had observation == null. These
    # checks pin the fix: a pushed snapshot reaches the step record, is tagged
    # with its provenance and age, and a snapshot passed with the request
    # overrides the carried one.
    runtime.observe({"map": 1234, "cell": 300, "hp": 100}, goal="watch the farm")
    result = runtime.handle_goal("stop everything now")
    steps = [json.loads(line) for line in
             (tmp / "runtime.jsonl").read_text(encoding="utf-8").splitlines()
             if json.loads(line).get("kind") == "step"]
    last = steps[-1]
    check("carried observation reaches the step record",
          last.get("observation") == {"map": 1234, "cell": 300, "hp": 100},
          json.dumps(last.get("observation")))
    check("carried observation is tagged",
          last.get("observation_source") == "carried", str(last.get("observation_source")))
    check("carried observation carries an age",
          isinstance(last.get("observation_age_ms"), (int, float)),
          str(last.get("observation_age_ms")))

    fresh = {"map": 9999, "cell": 42, "hp": 55}
    runtime.execute_tool("knowledge.search", {"query": "bouftou"}, goal="what is that",
                         observation=fresh)
    last = steps[-1] = [json.loads(line) for line in
                        (tmp / "runtime.jsonl").read_text(encoding="utf-8").splitlines()
                        if json.loads(line).get("kind") == "step"][-1]
    check("per-call observation wins over the carried one",
          last.get("observation") == fresh, json.dumps(last.get("observation")))
    check("per-call observation is tagged call",
          last.get("observation_source") == "call", str(last.get("observation_source")))
    check("per-call observation has age zero",
          last.get("observation_age_ms") == 0.0, str(last.get("observation_age_ms")))

    runtime.handle_goal("stop everything now", observation={"map": 7, "cell": 7, "hp": 7})
    last = [json.loads(line) for line in
            (tmp / "runtime.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("kind") == "step"][-1]
    check("handle_goal observation is fresh, not carried",
          last.get("observation_source") == "call"
          and last.get("observation_age_ms") == 0.0
          and last.get("observation", {}).get("map") == 7,
          json.dumps({k: last.get(k) for k in
                      ("observation_source", "observation_age_ms")}))
    check("observation counter advances", runtime.observations_seen >= 2,
          str(runtime.observations_seen))

    # -- §C: schedule objective as structured context -------------------------
    # The policy must confirm the quest_id the planner already chose, not guess
    # one from prose that never contained it.
    provider = lambda: {"tool": "quest.advance", "quest_id": 1935, "why": "step 4 of plan"}
    call = policy.choose("advance the quest please", objective=provider())
    check("quest.advance confirms schedule's quest_id via objective",
          call is not None and call.tool == "quest.advance"
          and call.args.get("quest_id") == 1935,
          f"{call.tool} {call.args}" if call else "no call")
    call = policy.choose("advance the quest please")
    check("without objective, quest.advance is never guessed from prose",
          not (call is not None and call.tool == "quest.advance"),
          f"{call.tool} {call.args}" if call else "no call")
    call = policy.choose("stop everything now", objective=provider())
    check("non-quest objective does not hijack other tools",
          call is not None and call.tool == "safe.stop",
          f"{call.tool} {call.args}" if call else "no call")
    # end-to-end: the runtime threads the objective_provider result into decide()
    runtime.objective_provider = provider  # type: ignore[assignment]
    result = runtime.handle_goal("please continue the quest")
    check("handle_goal threads the objective into the policy (§C e2e)",
          result.tool == "quest.advance", str(result.tool))
    runtime.objective_provider = None


def test_tool_dataset(tmp: Path) -> None:
    print("\n== tool-use dataset ==")
    records = [
        {"kind": "step", "tool": "quest.list", "ok": True, "source": "scripted",
         "goal": "list quests in Astrub", "instance": "t",
         "args": {"area": "Astrub", "dungeon_only": False, "limit": 5}},
        {"kind": "step", "tool": "knowledge.search", "ok": True, "source": "scripted",
         "goal": "what is a Bouftou?", "instance": "t",
         "args": {"query": "Bouftou", "kind": "monster", "limit": 3}},
        {"kind": "step", "tool": "inv.list", "ok": False, "source": "live",
         "goal": "show my inventory", "instance": "t", "error": "bridge down",
         "args": {"kind": "all"}},
        {"kind": "goal_unresolved", "goal": "blorb wibbity", "source": "live",
         "instance": "t"},
    ]
    out = tmp / "sft"
    stats = tool_dataset.write_dataset(records, out_dir=out, val_ratio=0.5, seed=1)
    check("dataset keeps only successful steps",
          stats["examples"] == 2 and stats["failed_steps"] == 1
          and stats["unresolved"] == 1,
          json.dumps({key: stats[key] for key in
                      ("examples", "failed_steps", "unresolved")}))
    check("dataset splits train/val", stats["train"] == 1 and stats["val"] == 1)

    rows = (tool_dataset.read_jsonl(out / "train.jsonl")
            + tool_dataset.read_jsonl(out / "val.jsonl"))
    example = rows[0]
    system = example["messages"][0]["content"]
    check("system prompt carries the tool contract",
          all(name in system for name in tool_names()))
    completion = json.loads(example["messages"][-1]["content"])
    ok, _args = validate_call(completion["tool"], completion["args"])
    check("assistant completion is a valid tool call", ok, str(completion))
    rebuilt = tool_dataset.build_example(records[0])
    check("example ids are stable", rebuilt["id"] == tool_dataset.build_example(records[0])["id"])


def test_synthetic_tools(tmp: Path) -> None:
    print("\n== synthetic full-tool data ==")
    out = tmp / "synthetic"
    stats = synthetic_tools.generate(count=64, seed=3, root=out)
    check("synthetic generator covers every client tool",
          stats["total"] == 64
          and set(stats["by_tool"]) == set(synthetic_tools.SYNTHETIC_TOOLS),
          json.dumps(stats["by_tool"]))
    check("synthetic generator reaches every language",
          set(stats["by_lang"]) == {"en", "fr", "es", "de"})
    rows = tool_dataset.read_jsonl(out / "synthetic.jsonl")
    check("synthetic rows are valid constructed steps",
          len(rows) == stats["ok"] and len(rows) >= 60 and all(
              row.get("source") == "synthetic"
              and row.get("ok") is True
              and validate_call(row.get("tool"), row.get("args") or {})[0]
              and (row.get("result") or {}).get("constructed") is True
              for row in rows))
    default_stats = tool_dataset.build_examples(rows)[1]
    allowed_stats = tool_dataset.build_examples(rows, sources=("synthetic",))[1]
    check("synthetic data is opt-in for SFT",
          default_stats["skipped_source"] == len(rows)
          and allowed_stats["examples"] == len(rows),
          json.dumps({"default_skipped": default_stats["skipped_source"],
                      "opt_in": allowed_stats["examples"]}))


def test_tool_eval() -> None:
    print("\n== tool-use benchmark ==")
    ids = [case["id"] for case in tool_eval.GOLD]
    check("gold case ids unique", len(ids) == len(set(ids)), f"{len(ids)} cases")
    check("gold tools exist in the schema",
          all(case["tool"] in TOOLS_BY_NAME for case in tool_eval.GOLD))
    bad_keys = [
        (case["id"], key)
        for case in tool_eval.GOLD
        for key in (case.get("args") or {})
        if key not in TOOLS_BY_NAME[case["tool"]].params
    ]
    check("gold argument keys exist", not bad_keys, str(bad_keys[:3]))

    oracle = {
        case["id"]: {"tool": case["tool"], "args": case.get("args") or {}}
        for case in tool_eval.GOLD
    }
    report = tool_eval.evaluate(oracle)["totals"]
    check("oracle predictions score 100%",
          report["tool_ok"] == report["cases"] == report["call_ok"],
          json.dumps(report))
    empty = tool_eval.evaluate({case["id"]: None for case in tool_eval.GOLD})["totals"]
    check("missing predictions score zero", empty["tool_ok"] == 0 and empty["call_ok"] == 0)


def test_thx_helpers() -> None:
    print("\n== THX-01 policy helpers (offline) ==")
    cases = {
        "stop everything now": "safe.stop",
        "beende alles": "safe.stop",
        "farm kanigs": "farm.start",
        "tell the team: ready": "chat.say",
        "message the group: on my way": "chat.say",
        "informa al equipo: listo": "chat.say",
        "what am I carrying?": "inv.list",
        "where am I?": "state.snapshot",
        "go to [12, 8]": "nav.goto",
        "list 5 Bouftou Wool at 900 kamas": "hdv.list",
        "precio de la Lana de Bouftou": "hdv.scan",
        "Notstopp: out of potions": "safe.stop",
        "arrêt d'urgence : out of potions": "safe.stop",
        "consulta el mercado para Lana de jalatí": "hdv.scan",
        "verkaufe 20 Gildenhallentrank auf dem Markt zum Marktpreis -1": "hdv.list",
        "biete 5 DrakDrak Shard 94 für 5000 Kamas an": "hdv.list",
        "quêtes de donjon vers Amakna": "quest.list",
        "liste mes objets": "inv.list",
    }
    for goal, expected in cases.items():
        got = thx._guard_tool(goal)
        check(f"guard {goal!r} -> {expected}", got == expected, f"got {got}")

    entity_goal = "what is a Bouftou?"
    if (REPO_ROOT / "data" / "cache" / "monsters_full.json").exists():
        check("guard knowledge.search uses entity index",
              thx._guard_tool(entity_goal) == "knowledge.search")
        for goal in ("look up Bouftou", "busca Bouftou", "suche Bouftou",
                     "trouve Bouftou", "encuentra Bouftou"):
            check(f"guard lookup verb {goal!r}",
                  thx._guard_tool(goal) == "knowledge.search",
                  f"got {thx._guard_tool(goal)}")
    else:
        skip("guard knowledge.search uses entity index", "monster cache not synced")

    check("guard plain quest list",
          thx._guard_tool("list quests in Astrub") == "quest.list")

    check("quantity from sell goal", thx._extract_quantity("sell 2 Gold Ingots") == 2)
    check("quantity beats price", thx._extract_quantity("mets en vente 4 Lingots d'Or à 800 kamas") == 4)
    check("quantity ignores quest id", thx._extract_quantity("advance quest 1696") is None)
    check("quantity ignores level", thx._extract_quantity("quests for level 40") is None)

    check("item cleans article", thx._clean_item("the Bouftou Wool") == "Bouftou Wool")
    check("item singularizes", thx._clean_item("Lanas de Bouftou") == "Lana de Bouftou")
    check("item strips quantity", thx._clean_item("2 Gold Ingots") == "Gold Ingot")

    check("zaap strips to", thx._extract_zaap("zaap to Bonta") == "Bonta")
    check("zaap strips de", thx._extract_zaap("zaap de Bonta") == "Bonta")
    check("zaap strips nach", thx._extract_zaap("nimm den Zaap nach Bonta") == "Bonta")

    check("chat text after colon", thx._extract_chat_text("tell the team: ready") == "ready")
    check("chat text message verb",
          thx._extract_chat_text("message the group: on my way") == "on my way")
    check("chat text keeps apostrophe",
          thx._extract_chat_text("préviens l'équipe : j'arrive") == "j'arrive")
    check("chat text inform verb",
          thx._extract_chat_text("informa al equipo: listo") == "listo")

    found = thx._find_entity("put 1 Bouftou Wool up for 750 kamas", "item")
    check("item lookup skips currency", found not in ("Kama", "Kamas"), f"got {found!r}")


def _find_checkpoint() -> Optional[Path]:
    """An encoder checkpoint to exercise the real inference path with.

    Looked up rather than hard-coded, because the checkpoint lives wherever the
    last training run put it and the check is worthless if it silently skips on a
    machine that has one. ``None`` means genuinely absent.
    """
    import os

    candidates = [
        Path(os.environ.get("DOFUS_ENCODER_CKPT", "")) if os.environ.get(
            "DOFUS_ENCODER_CKPT") else None,
        REPO_ROOT / "models" / "encoder30" / "model.pt",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "opencode"
        / "encoder_policy" / "model.pt",
        Path("C:/Users/sao/kaggle_outputs/encoder30/model.pt"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def _supplement_rows() -> List[Dict[str, Any]]:
    """The focused ``quest.advance`` supplement, if it has been generated.

    Used for checks that must run against real generator output rather than
    hand-picked examples -- which goals the language detector abstains on is
    not something a test should assume.
    """
    import json

    path = REPO_ROOT / "trajectories" / "synthetic_quests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_postprocess_serving() -> None:
    """The serving path must apply ``clean_call`` -- the measured gains live or
    die with this wiring, and the batch benchmark alone cannot see it."""
    print("\n== post-processing in the serving path ==")
    from ai.encoder_serve import EncoderToolPolicy
    from ai.postprocess import clean_call, extract_text

    class StubPolicy(EncoderToolPolicy):
        """``_predict`` replaced by a script, so no model is needed."""

        def __init__(self, script, **kwargs):
            self.backend = "stub"
            self.device = "cpu"
            self.fallback = None
            self.fill_reply_language = True
            self.postprocess = kwargs.get("postprocess", True)
            self.gazetteer = kwargs.get("gazetteer")
            self.kb = kwargs.get("kb")
            self.calls = self.refused = self.invalid = self.fallbacks = 0
            self.total_ms = self.postprocess_ms = self.repaired = 0
            self._script = list(script)

        def _predict(self, goal, observation):
            return self._script.pop(0) if self._script else None

    # -- rule 1: a missing required field is filled only when the goal grounds it.
    got = clean_call("hdv.list", {"item": "Parchemins"}, "liste 3 Parchemins de Vaelor")
    check("clean_call leaves a shorter span alone (prefix collision is a guess)",
          got == {"item": "Parchemins"}, str(got))
    got = clean_call("nav.goto", {}, "va à [12, -8]")
    check("clean_call reads a coordinate pair",
          got == {"x": 12, "y": -8}, str(got))
    got = clean_call("farm.start", {}, "farm pendant 45 minutes")
    check("clean_call never fills an optional int from a lone number",
          got == {}, str(got))

    # -- rule 2 on numbers: a truncated id is the same defect as a truncated
    #       span, and gets the same repair. Measured: 21 of 344 id-bearing
    #       quest.advance val rows came back as a strict prefix of the id the
    #       goal states (807 -> 80, 5340 -> 53).
    got = clean_call("quest.advance", {"quest_id": 80}, "continue quest 807")
    check("clean_call restores digits the decoder dropped",
          got == {"quest_id": 807}, str(got))
    got = clean_call("quest.advance", {"quest_id": 53}, "continue quest 5340")
    check("clean_call restores a dropped digit pair",
          got == {"quest_id": 5340}, str(got))
    # A value the goal actually states is grounded and must not be "repaired",
    # even though a longer number is also present.
    got = clean_call("quest.list", {"level_min": 5}, "quests de niveau 5 à 55")
    check("clean_call keeps a number the goal states",
          got == {"level_min": 5}, str(got))
    # The schema default is the canonical "not stated". Rewriting it invents a
    # number -- this is the guard that was missing on the first measurement,
    # where min_stock:1 became 1698 from an unrelated quest id in the goal.
    got = clean_call("inv.sell", {"item": "RhuQuor Essence", "min_stock": 1},
                     "vends RhuQuor Essence 14")
    check("clean_call never rewrites a value at its schema default",
          got.get("min_stock") == 1, str(got))
    # Two candidate extensions is a guess, and rule 3 forbids guessing.
    got = clean_call("quest.advance", {"quest_id": 5}, "quête 55 puis quête 57")
    check("clean_call refuses when two numbers extend the value",
          got.get("quest_id") == 5, str(got))

    # -- rule 1 on free text: an elision apostrophe is not a quote. Reading
    #       "l'équipe" as a quoted span made extract_text return "équipe : j"
    #       for "préviens l'équipe : j'arrive", and "urgence s" for
    #       "arrêt d'urgence s'il te plaît" -- the second of which invented a
    #       safe.stop reason out of a bare trigger.
    check("extract_text ignores an elision apostrophe",
          extract_text("préviens l'équipe : j'arrive") == "j'arrive",
          str(extract_text("préviens l'équipe : j'arrive")))
    check("extract_text returns None for a bare trigger",
          extract_text("arrêt d'urgence s'il te plaît") is None,
          str(extract_text("arrêt d'urgence s'il te plaît")))
    check("extract_text strips guillemets",
          extract_text("chuchucha a Quetzin: «Te espero junto al faro».")
          == "Te espero junto al faro",
          str(extract_text("chuchucha a Quetzin: «Te espero junto al faro».")))

    # -- rule 3: refuse to guess. A span the gazetteer cannot ground is kept.
    got = clean_call("hdv.list", {"item": "Zzzz Unknown Thing"}, "liste Zzzz Unknown Thing")
    check("clean_call keeps an ungroundable span", got == {"item": "Zzzz Unknown Thing"}, str(got))

    # -- the wiring: post-processing runs between _predict and validate_call.
    gaz = kb = None
    try:
        from ai.gazetteer import Gazetteer
        gaz = Gazetteer()
    except Exception:
        skip("serving-path gazetteer", "gazetteer unavailable")
    try:
        from planning.kb import KnowledgeBase
        if KnowledgeBase().stats()["quests"]:
            kb = KnowledgeBase()
    except Exception:
        skip("serving-path KB", "knowledge base unavailable")

    policy = StubPolicy(
        [{"tool": "nav.goto", "args": {}}],
        gazetteer=gaz, kb=kb,
    )
    call = policy.choose("va à [12, -8]", tools=["nav.goto"])
    check("serving path fills coordinates the model omitted",
          call is not None and call.args.get("x") == 12 and call.args.get("y") == -8,
          str(call.args if call else None))
    check("serving path counts the repair", policy.repaired == 1, str(policy.repaired))
    stats = policy.stats()["postprocess"]
    check("serving path reports the resolvers it holds",
          stats["enabled"] and stats["mean_ms"] is not None, json.dumps(stats))

    off = StubPolicy([{"tool": "nav.goto", "args": {}}], postprocess=False)
    call = off.choose("va à [12, -8]", tools=["nav.goto"])
    # With post-processing off the coordinates stay missing, ``validate_call``
    # rejects the call, and with no fallback the agent refuses. That refusal is
    # the honest outcome -- it is also exactly what the wiring prevents.
    check("postprocess=False leaves the call invalid (agent refuses)",
          call is None and off.invalid == 1, f"call={call} invalid={off.invalid}")

    # -- the real torch ``_predict``, not the stub. ``StubPolicy`` replaces
    #       ``_predict`` with a script, so every check above runs whether or not
    #       the genuine inference path works -- and it did not:
    #       ``EncoderDataset.batches`` indexed ``TOOL_INDEX[item["tool"]]``
    #       unconditionally, which raised ``KeyError: ''`` on the first live goal
    #       because ``_encode_row(infer=True)`` deliberately lets an inference row
    #       omit the tool. A serving path that crashes on every call scores
    #       perfectly in the tests, which is the worst possible failure mode.
    checkpoint = _find_checkpoint()
    if checkpoint is None:
        skip("live torch serving path", "no encoder checkpoint on disk")
    else:
        from ai.encoder_serve import EncoderToolPolicy

        live = EncoderToolPolicy(
            checkpoint=checkpoint,
            tokenizer=checkpoint.parent / "tokenizer.json",
            policy_json=checkpoint.parent / "policy.json",
            device="cpu",
        )
        errors = []
        for goal in ("continue quest 807", "va à [12, -8]", "arrête tout"):
            try:
                result = live.choose(goal, tools=None)
            except Exception as exc:  # a crash here is the bug, not the check
                errors.append(f"{goal!r} raised {type(exc).__name__}: {exc}")
                continue
            if result is None:
                errors.append(f"{goal!r} refused")
        check("live torch serving path answers without raising",
              not errors, "; ".join(errors[:3]))
        check("live serving path reports latency",
              live.stats()["mean_ms"] is not None, json.dumps(live.stats()))

    # -- resolver order: gazetteer first, KB only on a unique exact match.
    if gaz is not None:
        # What the gazetteer actually learned: every quest.advance goal in the
        # corpus states the id ("work on quest 18"), so the tail map can only
        # echo digits that are already in the goal. It cannot resolve a *name*.
        resolved = gaz.lookup_quest_id("work on quest 18")
        check("gazetteer resolves a quest id the goal states",
              resolved == 18, str(resolved))
        check("gazetteer cannot invent a quest id from a name",
              gaz.lookup_quest_id("avance la quête Oscuras intenciones") is None)
    if kb is not None:
        q = kb.quest(int(kb.quest_id[0]))
        hits = kb.find_quests(q.name, "fr", 3)
        check("KB find_quests round-trips a quest name",
              len(hits) == 1 and hits[0].id == q.id,
              f"{q.name!r} -> {[h.name for h in hits]}")
        check("KB quest() returns None for an unknown id", kb.quest(2 ** 31) is None)
        # The name -> id path the gazetteer cannot do: the KB holds the game's
        # own quest names, per language, so it resolves a name the goal states.
        goal = f"avance la quête {q.name}"
        got = clean_call("quest.advance", {}, goal, gaz, kb, lang="fr")
        check("KB resolves a quest name the gazetteer cannot",
              got == {"quest_id": q.id}, str(got))

    # -- a reason is filled only when the goal states one. The whole-goal
    #       fallback invented a reason for every bare trigger and turned 16
    #       correct safe.stop calls into wrong ones while *raising* the
    #       release-gate score, because containment forgave it.
    if gaz is not None:
        for goal, want in (
            ("parada de emergencia: low hp", {"reason": "low hp"}),
            ("stopp, too many monsters!", {"reason": "too many monsters"}),
            ("Notstopp bitte", {}),
            ("Stopp alle Aktionen, Notfall.", {}),
            ("arrêt d'urgence s'il te plaît", {}),
        ):
            got = clean_call("safe.stop", {}, goal, gaz, kb, lang="fr")
            check(f"reason from {goal!r} -> {want}", got == want, str(got))
        # Ambiguity is a refusal, not a guess: "Premi" prefixes five quests, so
        # no resolver may pick one.
        ambiguous = "Premi"
        got_amb = clean_call("quest.advance", {}, f"avance la quête {ambiguous}",
                             gaz, kb, lang="fr")
        check("clean_call refuses an ambiguous quest name", got_amb == {}, str(got_amb))
        check("clean_call refuses a name absent from the KB",
              clean_call("quest.advance", {}, "avance la quête Zzzz Inexistante",
                         gaz, kb, lang="fr") == {})

    # -- the resolver scans the goal for a known name instead of trying to
    #       isolate a span. Lead-verb stripping recovered 263 of 695
    #       name-bearing corpus rows; the scan recovers 657, because the quest
    #       noun sits mid-sentence in half the templates and names contain
    #       commas. These lock in the cases the stripping got wrong.
    if kb is not None:
        from ai.postprocess import resolve_quest_id
        probe = kb.find_quests("Wogew", "fr", 1)
        if probe:
            qid, qname = probe[0].id, probe[0].name
            for goal, lang in (
                (f"mach mit Quest {qname} weiter", "de"),        # noun mid-sentence
                (f"fais l'étape suivante de la quête {qname}", "fr"),
                (f"do the next step of quest {qname}", "en"),
                (f"erledige den nächsten Schritt von Quest {qname}", "de"),
            ):
                got = resolve_quest_id(goal, kb, lang)
                check(f"resolver reads a name out of {goal[:34]!r}",
                      got == qid, f"got {got} want {qid}")
        # The detector abstains on some goals, and a miss in the goal's own
        # language must not be a miss overall -- otherwise every "auto" row is
        # dropped for a reason that has nothing to do with the label. Take real
        # abstaining rows from the supplement corpus rather than assuming which
        # goals abstain, and count only the ones that actually name a quest: an
        # id-in-text goal has no name to find, and must resolve to nothing.
        import re as _re

        from ai.lang_detect import detect as _detect
        mishandled = []
        for row in _supplement_rows():
            goal, want = row["goal"], row["args"].get("quest_id")
            if _detect(goal).lang != "auto":
                continue
            if _re.search(r"(?<!\d)" + str(want) + r"(?!\d)", goal):
                # An id-in-text goal names no quest; anything the resolver
                # returns here was invented.
                if resolve_quest_id(goal, kb, "auto") is not None:
                    mishandled.append(("invented a name from a bare id", goal))
                continue
            got = resolve_quest_id(goal, kb, "auto")
            if got == want:
                continue
            if not kb.quests_named_in(goal, None):
                mishandled.append(("found no name in any language", goal))
            elif got is not None:
                mishandled.append((f"resolved to {got}, not {want}", goal))
            # else: candidates exist but are not unique -- a correct refusal,
            # the same one a player would have to make.
        check("resolver survives a detector abstention",
              not mishandled, "; ".join(f"{why}: {g!r}" for why, g in mishandled[:3]))
        # 34 distinct KB rows share the name "Merry Kwismas" in all five
        # languages. A player could not resolve that either; the resolver must
        # refuse rather than pick one of them.
        check("resolver refuses a name the KB duplicates",
              resolve_quest_id("advance quest Merry Kwismas", kb, "en") is None)
        # An id-in-text goal names no quest, so there is nothing to resolve.
        check("resolver finds no name in an id-bearing goal",
              resolve_quest_id("avance la quête 393", kb, "fr") is None)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dofus-agent-selftest-"))
    print(f"agent selftest — temp dir {tmp}")
    try:
        test_schema()
        test_recorder(tmp)
        test_knowledge(ToolExecutor(cache_dir=REPO_ROOT / "data" / "cache", timeout=0.3))
        test_quest_area()
        test_executor_offline(tmp)
        test_runtime(tmp)
        test_tool_dataset(tmp)
        test_synthetic_tools(tmp)
        test_tool_eval()
        test_thx_helpers()
        test_postprocess_serving()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED: {len(failed)} check(s): {', '.join(failed)}")
        return 1
    skipped = [name for name, ok, detail in RESULTS if ok and detail.startswith("skip:")]
    print(f"All {len(RESULTS)} checks passed ({len(skipped)} skipped).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

