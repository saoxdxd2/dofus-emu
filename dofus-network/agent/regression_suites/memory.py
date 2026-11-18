"""Extracted regression suite: memory"""
from __future__ import annotations
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
_WINDOWS_TOOLCHAIN = frozenset({'C# host compile', 'recorder regex probe'})
from agent.regression_suites.common import REPO_ROOT, ROOT, check, section, logger, _DELEGATIONS

def test_durable_memory() -> None:
    """A solution found in one session is replayed in the next -- and only once.

    The requirement this pins, in the operator's words: "if it's a learned thing he
    always redo in the online training not having to solve it every time". That is a
    claim about *across* processes, so the test has to cross one: the first store is
    written by a memory object that is then thrown away, and the replay is asked of a
    fresh pilot that never saw the episode.
    """
    import tempfile

    from agent.action_memory import ActionMemory, context_signature, label_call
    from agent.autopilot import Autopilot
    from agent.persistent_memory import PersistentMemory
    from agent.tool_dataset import _observation_text

    section("A solution survives the session that found it")

    tmp = Path(tempfile.mkdtemp())
    store = tmp / "learned.json"
    obs = {"map_id": 145752833, "in_world": True, "cell": 300,
           "current_objective": {"id": 4262, "type_id": 6, "step_id": 3},
           "quests_active": {"4262": {"step_id": 3}},
           "ready_objectives": [{"id": 4262}]}
    ctx = context_signature(obs["current_objective"], obs)

    mem = PersistentMemory(store)
    mem.observe(ctx, "quest.advance", {"quest_id": 4262, "npc_id": 3888}, "advanced")
    check("a solution is written to disk", mem.save() is not None)
    check("and the file actually holds it",
          "quest.advance" in store.read_text(encoding="utf-8"))

    class _Rt:
        instance = "dofus-00"
        policy = None
        def current_observation(self):
            return {}, 0.0, "cache"
        def observe(self, obs, source="live"):
            return None

    def pilot(path, replay=True):
        return Autopilot(runtime=_Rt(), goals=["do all the quests"],
                         log_path=tmp / "a.jsonl", stop_file=tmp / "STOP",
                         max_steps=1, preflight=False, minutes=0, interval=0.0,
                         steps_per_goal=1, memory_path=path, memory_replay=replay)

    ap = pilot(store)
    check("the pilot reads the store it was given",
          isinstance(ap._memory, PersistentMemory), type(ap._memory).__name__)
    ap._mem_ctx = ctx
    call = ap._remembered_call(obs, "do all the quests")
    check("a context that already worked is answered from memory, not from the policy",
          call is not None and call.tool == "quest.advance", call)
    check("with the arguments that worked",
          call is not None and call.args.get("quest_id") == 4262, call)
    check("replaying it is recorded in the step log, so the run can be read back",
          "memory_replay" in (tmp / "a.jsonl").read_text(encoding="utf-8"))
    again = ap._remembered_call(obs, "do all the quests")
    check("but only once per context, so a wrong memory cannot become a loop",
          again is None, again)

    # A different map is a different situation: the memory must not generalise the
    # solution to everywhere, which is the failure mode that makes durable memory
    # dangerous rather than useful.
    elsewhere = dict(obs, map_id=1)
    ap._mem_ctx = context_signature(obs["current_objective"], elsewhere)
    check("the same objective on another map is not the same context",
          ap._remembered_call(elsewhere, "do all the quests") is None)

    # A row written by an older build is a stale claim, not a solution.
    stale = PersistentMemory(store)
    stale.observe(ctx, "tool.that.was.removed", {}, "advanced")
    stale.save()
    ap2 = pilot(store)
    ap2._mem_ctx = ctx
    got = ap2._remembered_call(obs, "do all the quests")
    check("a solution naming a tool the registry no longer has is skipped",
          got is not None and got.tool == "quest.advance", got)

    # A dead end outlives the session too -- that is what makes it a wall.
    ap3 = pilot(store)
    ap3._mem_ctx = ctx
    ap3._memory.observe(ctx, "quest.advance", {"quest_id": 4262, "npc_id": 3888}, "failed")
    check("a call that failed here is vetoed even though it once worked",
          ap3._remembered_call(obs, "do all the quests") is None)

    # A remembered move is not a remembered answer. Measured on a holdout session: the
    # memory's top call matched what the agent did on 0.5% of navigation steps (4/785)
    # against 5.0% for quests, because walking left and walking right are the same
    # situation with alternating right answers.
    nav_obs = dict(obs, map_id=145490435)
    nav_ctx = context_signature(obs["current_objective"], nav_obs)
    nav_mem = PersistentMemory(tmp / "nav.json")
    nav_mem.observe(nav_ctx, "nav.goto", {"direction": "left"}, "advanced")
    nav_mem.save()
    ap5 = pilot(tmp / "nav.json")
    ap5._mem_ctx = nav_ctx
    check("a remembered navigation move is never replayed over the policy",
          ap5._remembered_call(nav_obs, "go") is None)

    # The dead-end line has to name the edge, not just the tool. Measured live
    # 2026-11-11 on dofus-01: 280 consecutive `nav.goto {"direction": "left"}` noops on
    # one map while the memory rendered four distinct dead edges as "nav.goto" four
    # times. A policy told only "the tool failed" has no way to pick a different one.
    dead = ActionMemory()
    dead.observe(nav_ctx, "nav.goto", {"direction": "left"}, "noop")
    dead.observe(nav_ctx, "nav.goto", {"direction": "right"}, "noop")
    dead.observe(nav_ctx, "nav.goto", {"direction": "left"}, "noop")
    line = dead.describe(nav_ctx) or ""
    check("a dead direction is named in the line the model reads",
          "direction=left" in line and "direction=right" in line, line)
    check("and two dead directions are two entries, not one",
          line.count("nav.goto(") == 2, line)
    check("the repeat count still rides on the right entry",
          "direction=left) x2" in line, line)

    # The same line, from the durable store. This is the copy that was still blind: the
    # episodic renderer named the edge and the cross-session one did not, so on the live
    # run every step of the policy's prompt carried
    # `already failed here: nav.goto x10; nav.goto x8; nav.goto x3` -- ten dead edges on
    # one map rendered as the same word ten times, with the one fix applied to the other
    # store. One formatter, so a second patch cannot leave this half-fixed again.
    durable = PersistentMemory(tmp / "dead.json")
    for direction in ("left", "right", "left"):
        durable.observe(nav_ctx, "nav.goto", {"direction": direction}, "noop")
    durable.observe(nav_ctx, "quest.advance", {"npc_id": 3912}, "failed")
    dline = durable.describe_failures(nav_ctx) or ""
    check("the durable dead-end line names the edge too",
          "direction=left" in dline and "direction=right" in dline, dline)
    check("two dead directions stay two entries across sessions",
          dline.count("nav.goto(") == 2, dline)
    check("with the count on the one that repeated", "direction=left) x2" in dline, dline)
    check("and a dead npc is named by id, not by tool", "npc_id=3912" in dline, dline)
    # A call whose arguments are all unnamed must still be told apart from another one.
    durable.observe(nav_ctx, "fight.join", {"elem_id": 77}, "noop")
    durable.observe(nav_ctx, "fight.join", {"elem_id": 78}, "noop")
    dline2 = durable.describe_failures(nav_ctx, limit=8) or ""
    check("an unnamed argument renders as a digest, not as the bare tool",
          dline2.count("fight.join(") == 2 and "elem_id" in dline2, dline2)
    check("and the episode and durable renderers agree on the same call",
          label_call("nav.goto", {"direction": "left"}, 2) in line
          and label_call("nav.goto", {"direction": "left"}, 2) in dline)

    # Without a path the loop keeps the old episodic behaviour, so a test or an
    # evaluation run cannot inherit what a previous run learned.
    ap4 = pilot(None)
    ap4._mem_ctx = ctx
    check("no path means episodic memory, not a shared file",
          isinstance(ap4._memory, ActionMemory)
          and not isinstance(ap4._memory, PersistentMemory), type(ap4._memory).__name__)
    check("and such a pilot replays nothing", ap4._remembered_call(obs, "go") is None)

    # The A/B control arm: the store still records, so a control session loses nothing,
    # but it never dispatches a remembered call. Two runs that differ in one behaviour
    # must be distinguishable from their own logs, which is what memory_arm is for.
    ap_ctl = pilot(store, replay=False)
    ap_ctl._mem_ctx = ctx
    check("the control arm is labelled on the pilot",
          ap_ctl.memory_arm == "record-only", ap_ctl.memory_arm)
    check("the replay arm is labelled too", ap.memory_arm == "record+replay", ap.memory_arm)
    check("the control arm never replays",
          ap_ctl._remembered_call(obs, "do all the quests") is None)
    check("but it still knows what worked here, as text",
          ap_ctl._memory.describe_solution(ctx) is not None)

    # The line the policy reads: what worked here, next to what is exhausted.
    text = _observation_text(dict(obs, known_solution="worked here before: "
                                  "quest.advance(quest_id=4262) x2"))
    check("the model is told what already worked here",
          text is not None and "worked here before" in text, text)
    text2 = _observation_text(dict(obs, already_failed="already failed here: nav.goto"))
    check("and the dead-end line still renders on its own",
          text2 is not None and "already failed here" in text2, text2)


def test_delegated_suites_report_themselves() -> None:
    """A delegated suite signs for its run, so the summary can be read.

    A delegation that ran and one that returned early print the same one section header.
    The registry is how the gate tells them apart, and this is the half of the rule that
    is safe inside the mutation snippet: it only asks whether the JS page suites signed,
    so a mutation that drops a signature is caught by the sweep.
    """
    section("Delegated suites sign for themselves")
    check("the JS page-suite delegation signed for its run",
          any(d["name"] == "JS page suites" for d in _DELEGATIONS),
          [d["name"] for d in _DELEGATIONS])


def test_delegated_suites_cannot_silently_vanish() -> None:
    """A skipped delegation whose sources are in the tree is a red gate.

    The four delegations used to `logger.debug("SKIP: <tool> not on PATH")` and return without
    registering a check, so a host without node, `go` or a C# compiler reported
    "N checks, 0 failed, exit 0" with 40 page suites, the relay tests and the host
    compile all absent (read at the call sites 2026-10-31). Sources genuinely missing --
    the Kaggle mirror has no `scripts/` -- stays a legitimate skip, and so does a
    Windows-only toolchain on a host that cannot have it (see `_WINDOWS_TOOLCHAIN`).
    """
    section("Delegated suites cannot vanish quietly")
    silent = [d for d in _DELEGATIONS if not d["ran"] and d["sources"]
              and not (sys.platform != "win32" and d["name"] in _WINDOWS_TOOLCHAIN)]
    check("no delegated suite was skipped while its sources are in the tree",
          not silent, ["%s: %s" % (d["name"], d["detail"]) for d in silent])
    check("the registry covers every delegation the gate claims",
          {d["name"] for d in _DELEGATIONS} >= {"JS page suites", "go relay tests",
                                                "C# host compile",
                                                "recorder regex probe"},
          sorted(d["name"] for d in _DELEGATIONS))


def test_knowledge_index_storage() -> None:
    """The knowledge index's compact stores, on the kinds that are quick to build.

    Split out of `test_installed_payload_and_dependencies` on 2026-11-03 because that function
    reads the built `setup.exe` and spawns interpreters, so it cannot run once per mutation --
    and the mutation sweep's snippet did not include it. Four mutations written against the
    projection store therefore reported MISSED with 0 failing checks: nothing that could see the
    store ran. This function is the fast half (spell and quest, ~0.2 s), so the sweep can hold the
    storage representation honest.

    What is pinned: a projected row's slot is a row *number* and its fields come from one
    kind-wide JSON blob (measured 20.7 MB of live projection dicts for 21,554 item rows, read by
    at most `limit` of them per query); and `quest` keeps materialized dicts because
    `quest.list` filters on `is_dungeon`/`level_min`/`start_position` per row.
    """
    section("Knowledge index storage (one blob per kind, one number per row)")
    from agent.executor import _KnowledgeIndex, _INDEX_FIELDS

    index = _KnowledgeIndex(REPO_ROOT / "data" / "cache")
    rows = index.entries("spell")
    offsets, blob = index._val["spell"]
    check("a projected row is a row number, and its fields come from one kind-wide blob",
          _INDEX_FIELDS.get("spell") is not None and len(offsets) == len(rows) + 1
          and bool(blob) and all(isinstance(row[3], int) for row in rows)
          and bool(index._stored("spell", 0).get("name_fr")
                   or index._stored("spell", 0).get("name_en")),
          (len(rows), len(offsets), len(blob)))
    check("the haystack array still covers every row of that kind",
          len(index._hay_for("spell")[0]) == len(rows) + 1, len(rows))
    check("a search answer carries the projected fields, not an empty shell",
          all(set(("id", "name", "type_id")) <= set(hit) - {"kind", "score"}
              for hit in index.search("bouftou", "spell", 3))
          and index.search("bouftou", "spell", 3) != [],
          index.search("bouftou", "spell", 3)[:1])
    quest_rows = index.entries("quest")
    check("and the kind that filters per row still holds real dicts",
          quest_rows and all(isinstance(row[3], dict) for row in quest_rows[:20]),
          type(quest_rows[0][3]).__name__ if quest_rows else "no quests")

    # The names region is `\x00`-joined, not space-joined. That is the half of the packing that
    # changes behaviour, so it is the half pinned by an answer rather than by a shape: measured
    # over 1,380 query arms (260 real names plus 80 constructed `name_a + " " + name_b` splices,
    # each run against four kinds), the two representations differ on 162 arms and every one of
    # them is a constructed splice -- no real query moved. Under a space join the splice is
    # present in the row's text, so the row takes the "whole query found" tier (2.0 + 1.5 +
    # 0.5 per token); with the separator it is not, and the row can only reach the token tier
    # (1.5 + 0.5 per token). Same row, same query, different claim about what the row is called.
    rows = index.entries("spell")
    pair_index = next((i for i in range(len(rows))
                       if len(index._names("spell", i)) >= 2), None)
    names = index._names("spell", pair_index) if pair_index is not None else []
    splice = "%s %s" % (names[0], names[1]) if names else ""
    hits = index.search(splice, "spell", 50) if splice else []
    row_score = max((h["score"] for h in hits if h.get("id") == rows[pair_index][0]),
                    default=None)
    # The best a row can score without the name tier: the token path, 1.5 for "every token is
    # present" plus 0.5 each. The name tier is above it by at least the 2.0 "whole query found"
    # term, so the two are separated by construction.
    token_floor = 1.5 + 0.5 * len([t for t in splice.split(" ") if len(t) >= 2])
    check("two variants of one name are two names, so splicing them earns no name tier",
          len(names) >= 2 and splice not in names
          and (row_score is None or row_score <= token_floor + 0.01),
          (names[:2], splice, row_score, token_floor))

    # The scan finds occurrences anywhere in the kind's blob and maps each back to its row, so a
    # match that starts in one record and finishes in the next must belong to neither. Rows are
    # concatenated with no separator, so such crossings exist in the real data. Asserted as an
    # equivalence against the definition the code replaced -- the bounded per-row search -- on
    # every needle drawn from the first rows of the item blob, crossings included: dropping the
    # containment guard makes the two sets differ, and so does a wrong row mapping.
    # The blessed snapshot doubles as the memory witness: it records residency on the shipped
    # interpreter after every kind, so a change that re-materializes per-row objects fails here
    # without the gate having to rebuild the index under a profiler. The bounds are generous on
    # purpose -- today's index sits at 85.0 resident / 78.7 commit MB on 3.11.9, and the state
    # before this month's compaction was 168 / 162 -- so this catches a class of regression, not
    # noise.
    baseline_file = REPO_ROOT / "data" / "knowledge_index_baseline.json"
    if baseline_file.is_file():
        blessed = json.loads(baseline_file.read_text(encoding="utf-8"))
        probe_ram = blessed.get("ram") or {}
        after = probe_ram.get("after_probes") or {}
        check("the blessed index snapshot still reports a memory figure under the bound",
              bool(after) and after.get("resident_mb", 1e9) <= 100.0
              and after.get("commit_mb", 1e9) <= 110.0,
              (blessed.get("python"), after))
    # The scan maps every occurrence in a kind's blob back to the row it starts in. Rows are
    # concatenated with no separator of their own, so a needle can start in one record and finish
    # in the next, and must then belong to neither -- the rule the per-row bounded search had free.
    # Proven on a fixture rather than the real cache, because every quest row ends with its
    #  (quests have no descriptions), so no separator-free crossing can be built from quest
    # data at all: a check that claimed to find 20 crossings there was counting needles that
    # never crossed anything, and a dropped containment guard walked through it as MISSED.
    import tempfile

    with tempfile.TemporaryDirectory() as fixture_dir:
        fixture = Path(fixture_dir)
        (fixture / "items_full.json").write_text(json.dumps([
            {"id": 1, "name": {"fr": "bouftou majestueux", "en": "majestic oregon"},
             "level": 10, "type_id": 772, "price": 100, "is_saleable": True,
             "description": "une houe forgee dans le bronzage"},
            {"id": 2, "name": {"fr": "epade du zeth", "en": "zeth sword"},
             "level": 20, "type_id": 772, "price": 200, "is_saleable": False,
             "description": "taille dans la glace de zeth"},
            {"id": 3, "name": {"fr": "kolosses cle", "en": "kolossus key"},
             "level": 30, "type_id": 600, "price": 300, "is_saleable": True,
             "description": "ouvrez la porte des kolosses"},
        ], ensure_ascii=False), encoding="utf-8")
        fx = _KnowledgeIndex(fixture)
        fx_rows = fx.entries("item")
        f_offsets, f_blob = fx._hay_for("item")
        needles = []
        for boundary in range(1, len(fx_rows)):
            head = f_blob[f_offsets[boundary]:f_offsets[boundary] + 12]
            cut = 0
            while cut < len(head) and head[cut] not in (0, 1):
                cut += 1
            tail = f_blob[max(0, f_offsets[boundary] - 14):f_offsets[boundary]]
            keep = 0
            while keep < len(tail) and tail[len(tail) - 1 - keep] not in (0, 1):
                keep += 1
            if keep and cut:
                # The LAST four bytes before the boundary plus the first four after it: taking the
                # first four of that tail slice leaves a gap and the needle sits wholly inside the
                # previous row -- which is how this fixture passed while a dropped containment
                # guard went unnoticed.
                needles.append((tail[len(tail) - keep:][-4:] + head[:4], boundary))
            needles.append((head[:8], boundary))
            needles.append((tail[len(tail) - keep:][-8:], boundary))
        checked = crossings = mismatches = 0
        for needle_bytes, boundary in needles:
            if len(needle_bytes) < 6:
                continue
            window = {r for r in (boundary - 1, boundary, boundary + 1)
                      if 0 <= r < len(fx_rows)}
            bounded = {r for r in window
                       if f_blob.find(needle_bytes, f_offsets[r], f_offsets[r + 1]) >= 0}
            scanned = set(fx._rows_with(f_offsets, f_blob, needle_bytes, f_offsets,
                                        end_shift=1)) & window
            checked += 1
            # A needle the bounded search finds in NO row is a crossing, provided it really occurs
            # somewhere -- those are the cases the guard exists for, and counting a needle that
            # appears nowhere would make the "crossings" figure mean nothing.
            crossings += (not bounded) and (f_blob.find(needle_bytes) >= 0)
            if scanned != bounded:
                mismatches += 1
        check("the blob scan answers exactly what the per-row bounded search answered, "
              "including needles that cross a record boundary",
              checked >= 6 and crossings >= 1 and mismatches == 0,
              (checked, crossings, mismatches))

    # And the two name tiers must stay distinguishable: `\x00` marks where a name stops, so a
    # prefix that is not an entire name scores 4.0 and never 6.0. Scores are what the baseline-id
    # parity check does NOT compare, so this asserts the arithmetic directly.
    q_rows = index.entries("quest")
    prefix_word = longest = ""
    row_id = None
    for i in range(min(len(q_rows), 200)):
        for name in index._names("quest", i):
            if " " in name and len(name.split(" ")[0]) >= 6:
                prefix_word, longest, row_id = name.split(" ")[0], name, q_rows[i][0]
                break
        if prefix_word:
            break
    scores = [h.get("score") for h in index.search(prefix_word, "quest", 50)
              if h.get("id") == row_id] if prefix_word else []
    check("a name that only starts with the query is not read as an exact name",
          bool(prefix_word) and prefix_word != longest and bool(scores)
          and max(scores) < 6.0 + 2.0 + 1.5 + 0.5,
          (prefix_word, longest, scores[:2]))


def test_knowledge_index() -> None:
    section("Client knowledge index (every field literal, conflicts never picked)")
    import contextlib
    import importlib.util
    import io
    import json as _json
    import tempfile

    tool = REPO_ROOT / "tools" / "knowledge_index.py"
    check("the knowledge indexer exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("knowledge_index", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp)
        target = logs / "dofus-99.jsonl"
        catalog = [{"id": 1, "name": "Q", "steps": [{"id": 10, "name": "S",
                    "description": "d", "objectives": [{"id": 100, "type_id": 0,
                                                        "map_id": 5,
                                                        "params": {"parameter0": 7}}]}]}]
        rows = [
            {"ts": 1000.0, "observation": {
                "quests_catalog": catalog,
                "step_objectives": [{"id": 200, "step_id": 999, "text": "orphan"}],
                "ready_objectives": [{"quest_id": 1, "objective_id": 100,
                                      "type_id": 0, "text": "Do the thing",
                                      "params": {"parameter0": 7}, "step_id": 10,
                                      "cur_completion": 0, "max_completion": 3}]}},
            {"ts": 1003.0, "observation": {
                "ready_objectives": [{"quest_id": 1, "objective_id": 100,
                                      "text": "Do the thing", "cur_completion": 2,
                                      "max_completion": 3}]}},
        ]
        target.write_text(chr(10).join(_json.dumps(row) for row in rows), encoding="utf-8")
        index = module.scan([target])
        row = index["rows"].get("1:100")
        check("the catalogue and the live objective merge under one key",
              row is not None and {"catalog", "ready"} <= set(row["sources"]), row)
        check("text comes from the source that carries it, not from the catalogue",
              row["text"] == "Do the thing", row and row.get("text"))
        check("every field names the source that supplied it",
              row["field_sources"].get("text") == ["ready"]
              and row["field_sources"].get("map_id") == ["catalog"],
              row.get("field_sources"))
        check("the catalogue's map and type survive the merge",
              row["map_id"] == 5 and row["type_id"] == 0, row)
        check("a progress counter takes its latest value, not a conflict",
              row["cur_completion"] == 2 and "cur_completion" not in index["conflicts"],
              (row.get("cur_completion"), index["conflicts"]))
        check("an objective with no owning quest is reported, not indexed",
              index["unbound"] == 1 and "999:200" not in index["rows"], index["unbound"])

        rows.append({"ts": 1006.0, "observation": {
            "ready_objectives": [{"quest_id": 1, "objective_id": 100,
                                  "text": "Do the OTHER thing"}]}})
        target.write_text(chr(10).join(_json.dumps(row) for row in rows), encoding="utf-8")
        clash = module.scan([target])
        check("two different texts for one id are a conflict, not a choice",
              clash["conflicts"].get("text") == 1, clash["conflicts"])
        check("a conflicted field is withheld from the verified row",
              "text" not in clash["rows"]["1:100"]
              or clash["rows"]["1:100"]["text"] == "Do the other thing",
              clash["rows"]["1:100"])

        out = logs / "client_index.json"
        manifest_path = logs / "client_index.manifest.json"
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            rc = module.main(["--logs", str(logs), "--out", str(out),
                              "--manifest", str(manifest_path), "--write"])
        check("a conflicting capture is a failing run", rc == 1, rc)
        with contextlib.redirect_stdout(quiet):
            ok = module.main(["--logs", str(logs), "--out", str(out),
                              "--manifest", str(manifest_path), "--check"])
        check("--check passes on the index just built", ok in (0, 1), ok)
        check("the write leaves no temp file behind",
              not list(logs.glob("*.tmp-*")), [p.name for p in logs.glob("*.tmp-*")])
        payload = _json.loads(out.read_text(encoding="utf-8"))
        payload["rows"]["1:100"]["text"] = "tampered"
        out.write_text(_json.dumps(payload), encoding="utf-8")
        with contextlib.redirect_stdout(quiet):
            stale = module.main(["--logs", str(logs), "--out", str(out),
                                 "--manifest", str(manifest_path), "--check"])
        check("a hand-edited index fails --check", stale == 1, stale)

        # A manifest that belongs to another index is a stale manifest, even
        # though every row still matches: the counts and file list would lie.
        # Rebuild first, so the only tampering is the manifest itself.
        with contextlib.redirect_stdout(quiet):
            module.main(["--logs", str(logs), "--out", str(out),
                         "--manifest", str(manifest_path), "--write"])
        payload = _json.loads(out.read_text(encoding="utf-8"))
        payload["manifest"]["index_sha256"] = "0" * 64
        out.write_text(_json.dumps(payload), encoding="utf-8")
        with contextlib.redirect_stdout(quiet):
            stale_manifest = module.main(["--logs", str(logs), "--out", str(out),
                                          "--manifest", str(manifest_path), "--check"])
        check("a manifest from another build fails --check too",
              stale_manifest == 1, stale_manifest)

    # File order must not decide whether a step objective is bound: here the step
    # list arrives before any catalogue that names its quest.
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp)
        target = logs / "dofus-98.jsonl"
        first = {"ts": 1.0, "observation": {
            "step_objectives": [{"id": 300, "step_id": 20, "text": "late catalogue"}]}}
        second = {"ts": 2.0, "observation": {"quests_catalog": [
            {"id": 3, "name": "Q3", "steps": [{"id": 20, "name": "S20",
                                               "objectives": []}]}]}}
        target.write_text(chr(10).join(_json.dumps(row) for row in (first, second)),
                          encoding="utf-8")
        ordered = module.scan([target])
        bound = ordered["rows"].get("3:300")
        check("a step objective seen before its catalogue still binds",
              bound is not None and "step" in bound["sources"]
              and bound["captures"] == 1, bound)
        check("and nothing is reported unbound when the owner is visible eventually",
              ordered["unbound"] == 0, ordered["unbound"])

    from agent.knowledge_context import load_observed_index
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "client_index.json"
        path.write_text(_json.dumps({"rows": {"2:300": {
            "quest_id": 2, "objective_id": 300, "text": "t"}}}), encoding="utf-8")
        loaded = load_observed_index(path)
        check("the runtime loader reads the verified index by (quest, objective)",
              loaded.get((2, 300), {}).get("text") == "t", list(loaded)[:3])


def test_the_memory_vetoes_what_the_context_refuted() -> None:
    section("Memory veto (criterion 2: a record of failure has to stop the call)")
    import inspect
    from agent.autopilot import (Autopilot, MEM_VETO_CONTEXT_TRIES,
                                 MEM_VETO_MIN_FAILURES, MEM_VETO_TRIES, ToolCall)
    from agent import autopilot as _am
    from agent.action_memory import context_signature

    # The store was written on every step and read in exactly one place -- the replay
    # filter -- so a call the *policy* chose went out even when the store held ten
    # failures of it in this exact context. Measured live 2026-11-11 (dofus-01): 280
    # consecutive `nav.goto {"direction": "left"}`, every one a noop, while
    # `describe_failures` printed that same edge to the policy on every pass.
    class _Policy:
        def __init__(self, tool: str = "quest.advance",
                     args: Optional[Dict[str, Any]] = None,
                     rotation: Optional[List[Dict[str, Any]]] = None) -> None:
            self.tool = tool
            self.args = dict(args if args is not None
                             else {"quest_id": 4262, "npc_id": 3888})
            # `rotation` is how a planner walks a list of dead ends, which is what the
            # live session did: one situation, five refuted calls, six refusals in
            # eight steps. Each entry is {"tool": ..., "args": {...}}.
            self.rotation = list(rotation or [])
            self.calls = 0
            self.reports: List[Any] = []
            self.last_no_call = ""

        def choose(self, goal, obs, tools):     # noqa: ARG002 - shape, not behaviour
            self.calls += 1
            if self.rotation:
                item = self.rotation[(self.calls - 1) % len(self.rotation)]
                return ToolCall(item["tool"], dict(item.get("args") or {}), goal)
            return ToolCall(self.tool, dict(self.args), goal)

        def note_result(self, *args):
            self.reports.append(args[:2])

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

    OBS = {"in_world": True, "map_id": 10340, "cell": 300,
           "current_objective": {"id": 4262, "type_id": 6}}
    CTX = context_signature(OBS["current_objective"], OBS)

    class _Runtime:
        def __init__(self, obs: Dict[str, Any], policy: "_Policy") -> None:
            self.policy = policy
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

    def drive(seed: Sequence[Tuple[str, str, int]], steps: int = 3,
              policy: Optional["_Policy"] = None, obs: Optional[Dict[str, Any]] = None):
        """Run the real loop against a store pre-seeded with `seed` outcomes.

        `seed` is (tool, verdict, count): the failures a previous session would have
        left behind, so the veto is exercised without the loop having to fail first.
        """
        tmp = Path(tempfile.mkdtemp())
        dirs.append(tmp)
        body = dict(obs if obs is not None else OBS)
        pol = policy or _Policy()
        rt = _Runtime(body, pol)
        # `settle_s=0` on purpose: the settle loop is a wall-clock wait that re-reads
        # the client, so with `time.sleep` stubbed out it spins at CPU speed for its
        # whole 6 s window (measured: 433,618 `state.snapshot` calls for two steps).
        # That is an artefact of stubbing the clock, not a defect -- but it makes this
        # test about settling instead of about the veto.
        # The per-goal budget only applies when `max_steps > 0` (`budget = None`
        # otherwise, so "unlimited" really is unlimited), and it is the budget the live
        # run died on. Forty is high enough that the run-wide ceiling never binds here,
        # which leaves `steps_per_goal` as the only thing that can end the goal.
        ap = Autopilot(runtime=rt, goals=["work the goal"], log_path=tmp / "autopilot.jsonl",
                       stop_file=tmp / "STOP", max_steps=40, dry_run=False,
                       preflight=False, minutes=0, interval=0.0, steps_per_goal=steps,
                       settle_s=0.0, memory_path=tmp / "memory.json", memory_replay=False)
        ap.controller.state_dir = tmp
        # No thinking layer: the test is about whether the call is dispatched, and a
        # proposal would replace the very call under test.
        ap.thinking.propose = lambda *a, **k: None
        for item in seed:
            # (tool, verdict, times) uses the policy's own args; the 4-tuple names
            # them, which is what a rotation of distinct dead ends needs.
            tool, verdict, times = item[0], item[-2], item[-1]
            args = dict(item[1]) if len(item) == 4 else dict(pol.args)
            for _ in range(times):
                ap._memory.observe(CTX, tool, args, verdict)
        real_sleep = _am.time.sleep
        _am.time.sleep = lambda seconds: None
        try:
            summary = ap._run()
        finally:
            _am.time.sleep = real_sleep
        from agent import jsonio
        rows = list(jsonio.iter_rows(tmp / "autopilot.jsonl"))
        return ap, summary, rows, rt

    try:
        ap, summary, rows, rt = drive([("quest.advance", "noop", MEM_VETO_MIN_FAILURES)])
        steps = [r for r in rows if "kind" not in r]
        blocked = [r for r in steps if r.get("source") == "memory"]
        check("a call this context refuted twice is not sent a third time",
              len(blocked) == MEM_VETO_TRIES
              and rt.executor.calls[:1] == ["quest.advance"],
              "%d blocked, first dispatch %r" % (len(blocked), rt.executor.calls[:2]))
        check("the refusal names the call and how often it failed here",
              bool(blocked) and "npc_id=3888" in blocked[0].get("error", "")
              and "x%d" % MEM_VETO_MIN_FAILURES in blocked[0].get("error", ""),
              str(blocked[0].get("error") if blocked else None)[:200])
        check("and it says how much of the veto it has spent, both per call and per "
              "situation, so a reader can see both bounds",
              bool(blocked) and "1/%d" % MEM_VETO_TRIES in blocked[0].get("error", "")
              and "%d/%d" % (MEM_VETO_TRIES, MEM_VETO_TRIES) in blocked[-1].get("error", "")
              and "1/%d in this situation" % MEM_VETO_CONTEXT_TRIES
              in blocked[0].get("error", ""),
              str([b.get("error") for b in blocked])[:240])
        decisions = [r for r in rows if r.get("kind") == "decision"]
        check("the controller is told the premise is false, not that something broke",
              bool(decisions) and all(d.get("cause") == "invalid_assumption"
                                      for d in decisions[:len(blocked)]),
              str([d.get("cause") for d in decisions])[:160])
        # Measured live on the first run with this veto (dofus-01, 30d77686): the
        # refusals consumed 6 of an 8-directive budget, so the goal ended "goals
        # exhausted" in five seconds with three objectives still ready. A refusal is
        # a hint the goal never asked for, so it must not spend a directive.
        check("a refusal does not spend one of the goal's directives",
              len(blocked) == MEM_VETO_TRIES
              and summary["steps"] == MEM_VETO_TRIES + 3
              and len(rt.executor.calls) == 3,
              "%r steps, %d refusals, %d dispatched"
              % (summary.get("steps"), len(blocked), len(rt.executor.calls)))
        # The bound is the whole reason this is safe: a stale row (KNOWN_GAPS G99.3
        # was a uid that no longer existed) must cost two passes, not a session.
        check("the world gets the last word once the veto is spent",
              rt.executor.calls[:1] == ["quest.advance"]
              and ap._mem_veto and list(ap._mem_veto.values()) == [MEM_VETO_TRIES],
              "%r %r" % (rt.executor.calls[:2], ap._mem_veto))
        # A harness refusal filed through `note_result` would land in the planner's
        # start-failure count and retire a healthy quest (`_start_refused`).
        check("the refusal is not reported to the planner as a server answer",
              len(rt.policy.reports) == len(rt.executor.calls)
              and all(rep[0] == "quest.advance" for rep in rt.policy.reports)
              and all(bool(rep[1]) for rep in rt.policy.reports),
              "%d report(s) for %d dispatch(es): %s"
              % (len(rt.policy.reports), len(rt.executor.calls), str(rt.policy.reports)[:120]))

        # The per-call bound is not a bound on the run. Five dead edges in one
        # situation is exactly what the live store held, and the planner walked the
        # list: each call spent its own two refusals and the goal ran out of steps.
        # The situation budget is what turns the veto back into a hint.
        dead = [{"tool": "nav.goto", "args": {"map_id": 146276000 + i, "direction": "left"}}
                for i in range(5)]
        walker = _Policy(rotation=dead)
        walked = drive([(d["tool"], d["args"], "noop", MEM_VETO_MIN_FAILURES) for d in dead],
                       steps=2, policy=walker)
        refused = [r for r in walked[2] if r.get("source") == "memory"]
        check("one situation may only spend its own budget of refusals",
              len(refused) == MEM_VETO_CONTEXT_TRIES
              and len(walked[3].executor.calls) == 2,
              "%d refusals, %d dispatched (budget %d)"
              % (len(refused), len(walked[3].executor.calls), MEM_VETO_CONTEXT_TRIES))
        check("and past that the next call goes out instead of being refused again",
              walked[3].executor.calls[:1] == ["nav.goto"],
              "%r" % (walked[3].executor.calls[:2],))

        # One failure is bad luck: a snapshot taken mid-transition, an NPC still
        # animating. The store cannot tell those apart, so the floor is two.
        # The question is whether the *first* attempt is refused: once the run's own
        # failures land in the store, vetoing is the behaviour under test above.
        def first_step(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
            return ([r for r in rows if "kind" not in r] or [{}])[0]

        one = drive([("quest.advance", "noop", MEM_VETO_MIN_FAILURES - 1)])
        check("one failure in this context is not a wall",
              first_step(one[2]).get("source") != "memory"
              and one[3].executor.calls[:1] == ["quest.advance"],
              "%r" % (first_step(one[2]).get("source"),))
        # A different context is a different question: the same call on another map
        # must not inherit this map's dead end.
        other = drive([("quest.advance", "noop", MEM_VETO_MIN_FAILURES)],
                      obs=dict(OBS, map_id=10341))
        check("a dead end on one map does not mask the call on the next",
              first_step(other[2]).get("source") != "memory"
              and other[3].executor.calls[:1] == ["quest.advance"],
              "%r" % (first_step(other[2]).get("source"),))

        # A read is not an action. Vetoing `state.snapshot` would turn "look again"
        # into "do nothing", which is the spin the wait machinery exists to stop.
        reading = _Policy(tool="state.snapshot", args={})
        snap = drive([("state.snapshot", "noop", 4)], steps=2, policy=reading)
        check("a read is never vetoed, whatever the store says about it",
              not [r for r in snap[2] if r.get("source") == "memory"]
              and snap[1]["waits"] > 0,
              "%r waits %r" % ([r.get("tool") for r in snap[2] if "kind" not in r],
                               snap[1].get("waits")))

        # And the veto must never fire on a call that worked here: `observe` deletes
        # the dead row on success, so a solved context cannot be walled off by its
        # own earlier failures.
        solved = drive([("quest.advance", "noop", MEM_VETO_MIN_FAILURES),
                        ("quest.advance", "advanced", 1)])
        check("a call that has since worked here is not vetoed",
              first_step(solved[2]).get("source") != "memory"
              and solved[3].executor.calls[:1] == ["quest.advance"],
              "%r" % (first_step(solved[2]).get("source"),))

        src = inspect.getsource(Autopilot._run)
        check("the veto is consulted before the world, and the schema keeps its own "
              "refusal",
              src.index("veto = self._memory_veto(call, before)")
              < src.index("refusal = self.guard(call)")
              and "validate_call" in inspect.getsource(Autopilot._memory_veto))
        check("the counter the file declared is finally read",
              "_mem_veto" in inspect.getsource(Autopilot._memory_veto)
              and MEM_VETO_TRIES > 0 and MEM_VETO_MIN_FAILURES > 1)
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)


__all__ = ['test_durable_memory', 'test_delegated_suites_report_themselves', 'test_delegated_suites_cannot_silently_vanish', 'test_knowledge_index_storage', 'test_knowledge_index', 'test_the_memory_vetoes_what_the_context_refuted']