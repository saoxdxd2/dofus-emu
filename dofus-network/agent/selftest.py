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

