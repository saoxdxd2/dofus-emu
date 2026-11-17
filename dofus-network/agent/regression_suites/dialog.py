"""Extracted regression suite: dialog"""
from __future__ import annotations
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from agent.regression_utils import code_only
from agent.regression_suites.common import REPO_ROOT, ROOT, check, section, logger

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

    # (3) nothing to go on (no overlap, no question match) => the first reply, not an
    # abstain. This check used to demand `abstain`, and `status: abstain` is not the
    # neutral choice it reads as: the planner's wait path (planner.py:2092) closes the
    # dialog when the reply list is definitively empty, and six observations later it
    # answers reply 0 regardless. `tools/test_dialog_policy.test_open_ended_tie_never_abstains`
    # pins the same decision from the other side, and
    # `test_planner_dialog_fallback_never_closes_when_replies_exist` is the row that
    # exists because abstaining once closed a dialog with replies on screen.
    ambiguous = rank_dialog(obs("quel temps fait-il ?",
                                [rp(0, "alpha"), rp(1, "beta")]))
    check("a tie with no signal takes the first reply rather than idling on it",
          ambiguous.get("status") == "choose"
          and ambiguous["reply"]["index"] == 0
          and ambiguous.get("reason") == "first open-ended reply in tie", ambiguous)

    # (4) a dialog that is not ready waits rather than choosing
    not_ready = rank_dialog({"dialog": {"open": True, "text_ready": False,
                                        "replies": [rp(0, "x")]}})
    check("an unfinished dialog waits", not_ready.get("status") == "wait", not_ready)

    # (5) a closed / absent dialog returns None
    check("no open dialog returns None", rank_dialog({"dialog": {"open": False}}) is None)
    check("non-dict input returns None", rank_dialog(None) is None)

    # -- the live trap of 2026-10-29 -------------------------------------------
    # In the Albuera solo area the only way out was an NPC reply. The old order
    # answered the courtesy line ("Wish him good luck."), closed the dialog and
    # left the loop on a map whose exits, groups and interactives were all empty.
    def live(message, replies, objective=None, map_id=100, need=None, type_=0):
        base = obs(message, replies, objective=objective)
        base["map_id"] = map_id
        base["current_objective"] = {"quest_id": 7, "text": objective or "",
                                     "type_id": type_}
        if need is not None:
            base["current_objective"]["map_id"] = need
        return base

    leaving = rank_dialog(live(
        "Welcome to Albuera.",
        [rp(0, "Enter. {icon:iconUpdateQuestMain}"),
         rp(1, "Bow and take your leave.")],
        objective="Talk to Lieutenant Ek Spyr, northeast of the Great Jarl's house.",
        need=555))
    check("an objective on another map prefers the reply that leaves",
          leaving.get("status") == "choose" and leaving["reply"]["index"] == 1,
          leaving)


    courtesy = rank_dialog(live(
        "Ordyn is a good leader, but I wish he'd listen to me a bit more...",
        [rp(0, "Ask what he means by that."), rp(1, "Wish him good luck.")],
        objective="Start a fight with a monster."))
    check("a courtesy line never ends a conversation while an objective is open",
          courtesy.get("status") == "choose" and courtesy["reply"]["index"] == 0,
          courtesy)

    marked = rank_dialog(live(
        "What will you do?",
        [rp(0, "Admire the view."), rp(1, "Take it. {icon:iconUpdateQuestMain}")],
        objective="Find smith"))
    check("a quest-marked reply wins an otherwise even tie",
          marked.get("status") == "choose" and marked["reply"]["index"] == 1
          and marked["reason"] == "quest-progress reply",
          marked)


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
            self._obs_source = "call"
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
    check("and the label says which read produced it",
          row["after_source"] == "call", str(row.get("after_source")))
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
              "fight_estimate, fight_outcome = self._track_fight(before, after"))
    summary_src = inspect.getsource(Autopilot.summary)
    check("the summary reports dialog attempts and stalls",
          '"dialogs": self._dialogs' in summary_src
          and '"dialog_stalls": self._dialog_stalls' in summary_src)


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


def test_dialog_label_reader() -> None:
    section("Dialog label reader (G23: the census behind a published verdict)")
    import importlib.util
    import json as _json

    tool = REPO_ROOT / "tools" / "dialog_labels.py"
    check("the reader exists", tool.exists(), str(tool))
    spec = importlib.util.spec_from_file_location("dialog_labels", tool)
    dl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dl)

    tmp = Path(tempfile.mkdtemp())
    try:
        def step(obs, args, source="call"):
            return _json.dumps({"kind": "step", "ts": 10.0, "instance": "gate",
                                "tool": "quest.advance", "args": args, "ok": True,
                                "error": None, "observation": obs,
                                "observation_source": source,
                                "result": {"response": {"ok": True}}}) + "\n"

        page = {"open": True, "npc_id": 7, "name_id": "Sorcerer",
                "message": "Pick one", "reply_count": 2,
                "replies": [{"index": 0, "text": "Yes", "reply_id": 10},
                            {"index": 1, "text": "No", "reply_id": 11}]}
        obs_with = lambda d, **kw: dict({"dialog": d, "current_objective": {
            "objective_id": 14070, "type": "QuestObjectiveFreeForm", "type_id": 0}}, **kw)
        log = tmp / "default.jsonl"
        log.write_text(
            step(obs_with(page), {"quest_id": 1, "reply": 1, "reply_text": "No"})
            + _json.dumps({"kind": "observation", "ts": 10.4,
                           "observation": obs_with({"open": True, "message": "Second page"},
                                                   objectives_completed=3),
                           "observation_source": "call"}) + "\n"
            # a second dispatch whose only follower is a carried read of the same page
            + step(obs_with(page), {"quest_id": 1, "close": True})
            + _json.dumps({"kind": "step", "ts": 11.0, "tool": "nav.goto", "args": {},
                           "observation": obs_with(page),
                           "observation_source": "carried"}) + "\n",
            encoding="utf-8")
        got = dl.pairs(str(log))
        check("a reply dispatch paired with a fresh read is labelled advanced",
              len(got) == 2 and got[0]["outcome"] == "advanced"
              and got[0]["asked"] == 1 and got[0]["reply_id"] == 11
              and got[0]["reply_text"] == "No" and got[0]["reply_count"] == 2,
              str(got[:1]))
        check("the objective type travels with the reconstructed pair",
              got[0]["objective_type"] == "QuestObjectiveFreeForm"
              and got[0]["objective_id"] == 14070, str(got[0]))
        check("the pair says which kind of read produced its label",
              got[0]["after_source"] == "call" and got[1]["after_source"] == "carried",
              str([r["after_source"] for r in got]))
        check("a close ask with no reply id is still a pair, and not attributed to a reply",
              got[1]["asked"] == "close" and got[1]["reply_id"] is None, str(got[1]))

        play = tmp / "play_dofus-99.jsonl"
        def ev(seq, kind, name, args=None, session="s"):
            return _json.dumps({"type": "PLAY_LOG", "session": session, "seq": seq,
                                "at": 1000 + seq, "event": dict({"kind": kind,
                                                                 "name": name},
                                                                **({"args": args} if args
                                                                   else {}))}) + "\n"
        play.write_text(
            ev(1, "send", "NpcDialogReplyMessage", {"replyId": 10})
            + ev(2, "recv", "QuestStepValidatedMessage")
            + ev(3, "send", "NpcDialogReplyMessage", {"replyId": 11})
            + ev(4, "recv", "LeaveDialogMessage")
            + ev(5, "send", "NpcDialogReplyMessage", {"replyId": 12})
            + ev(6, "send", "NpcGenericActionRequestMessage")
            + ev(7, "send", "LeaveDialogRequestMessage") + "\n", encoding="utf-8")
        wire = dl.wire_outcomes([str(play)])
        check("a reply the server credited is labelled from the wire, not the cache",
              wire[10]["wire_outcome"] == "quest progress", str(wire.get(10)))
        check("a reply answered by a leave is labelled dialog ended",
              wire[11]["wire_outcome"] == "dialog ended", str(wire.get(11)))
        check("a reply cut off by the next action owns no response",
              wire[12]["wire_outcome"] == "no response recorded", str(wire.get(12)))
        check("the wire counts sends per reply id, so a repeated ask is visible",
              wire[10]["sends"] == 1, str(wire[10]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_talk_needs_resolved_actor() -> None:
    section("NPC talk (a resolved actor, not a guessed id)")
    # Live 2026-10-28 20:22, map 145490944: NPC 3900 ("Roki") is actorId -4 in
    # the client model, and the client's own contextual menu sends that value as
    # `npcId`. Negative actor ids are this build's normal NPC ids, not a
    # sentinel; refusing them made every NPC talk impossible (quest 4262
    # objective 13952 could not even start). What must never be sent is an id
    # with no actor behind it, so the page now resolves the live actor and only
    # refuses when there is neither a finite id nor an actor.
    page = code_only((ROOT / "scripts" / "mobile-disguise.js").read_text(
        encoding="utf-8", errors="replace"))
    check("a negative actor id is no longer treated as unresolved",
          "Number(npc.contextualId) < 0" not in page,
          "NPCs have negative contextual ids on this build")
    check("the page resolves the live actor for the id it will name",
          "amResolve.getActor(resolvedId)" in page
          and "isFinite(resolvedId)" in page,
          "an id with no actor behind it must still be refused")
    check("the refusal names the template and the bad id",
          "npc_contextual_id: npc.contextualId" in page,
          "a bare failure cannot tell a missing actor from a wrong map")


__all__ = ['test_dialog_ranking', 'test_prompt_fidelity', 'test_named_mob_focus', 'test_dialog_labels', 'test_dialog_repeat_guard', 'test_dialog_label_reader', 'test_talk_needs_resolved_actor']