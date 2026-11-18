"""Bounded source-ordered facts and revisable action outcomes, not latent-state inference."""
from __future__ import annotations

import copy
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.action_memory import call_signature, label_call


CHANNELS = {"last_nav": "nav.goto", "last_talk": "quest.advance",
            "last_fight_action": "fight.join", "__agentLastNav": "nav.goto",
            "__agentLastTalk": "quest.advance", "__agentLastFight": "fight.join"}


def source_stamp(observation: Any) -> Optional[tuple[str, int]]:
    source = observation.get("source_snapshot") if isinstance(observation, dict) else None
    if not isinstance(source, dict):
        return None
    epoch, sequence = source.get("epoch"), source.get("sequence")
    if (not isinstance(epoch, str) or not epoch or len(epoch) > 128
            or type(sequence) is not int or sequence < 1):
        return None
    return epoch, sequence


def _projection(observation: dict) -> dict:
    position = observation.get("position")
    position = position if isinstance(position, dict) else {}
    return {
        "map_id": observation.get("map_id") if observation.get("map_id") is not None else position.get("map_id"),
        "cell": observation.get("cell") if observation.get("cell") is not None else position.get("cell"),
        "in_dialog": observation.get("in_dialog"), "in_fight": observation.get("in_fight"),
        "auto_fight": observation.get("auto_fight"),
        "objectives_completed": observation.get("objectives_completed"),
    }


@dataclass
class EvidenceUpdate:
    status: str
    adopt: bool = False
    revisions: list[dict] = field(default_factory=list)


class EvidenceMemory:
    def __init__(self, frames: int = 32, events: int = 256, actions: int = 64):
        if min(frames, events, actions) < 1:
            raise ValueError("evidence capacities must be positive")
        self.frame_capacity, self.event_capacity, self.action_capacity = frames, events, actions
        self.epoch: Optional[str] = None
        self.retired_epochs: set[str] = set()
        self.frames: dict[int, dict] = {}
        self.events: dict[int, dict] = {}
        self.actions: OrderedDict[str, dict] = OrderedDict()
        self.frame_floor = self.event_floor = 0
        self.latest_sequence = 0
        self._order = 0

    def register(self, action_id: str, tool: str, args: dict, context: tuple) -> list[dict]:
        if not self.epoch or not isinstance(action_id, str) or not action_id:
            return []
        existing = self.actions.get(action_id)
        identity = (self.epoch, tool, args, tuple(context))
        if existing is not None:
            if (existing["epoch"], existing["tool"], existing["args"], tuple(existing["context"])) != identity:
                raise ValueError("an action id cannot change identity")
            return []
        self._order += 1
        self.actions[action_id] = {
            "action_id": action_id, "epoch": self.epoch, "tool": tool,
            "args": copy.deepcopy(args), "context": list(context), "order": self._order,
            "provisional": None, "status": "pending", "verdict": None,
            "revision": 0, "evidence": [], "alternatives": [],
        }
        while len(self.actions) > self.action_capacity:
            self.actions.popitem(last=False)
        return self._reconstruct(action_id)

    def observe_outcome(self, action_id: str, verdict: Optional[str], observation: Any = None) -> list[dict]:
        action = self.actions.get(action_id)
        if action is None:
            return []
        previous = action.get("outcome_observation")
        stamp = source_stamp(observation)
        if stamp is not None and stamp[0] == action["epoch"]:
            action["outcome_observation"] = dict(_projection(observation),
                                                source_snapshot=copy.deepcopy(observation["source_snapshot"]))
        action["provisional"] = "advanced" if verdict == "advanced" else None
        revisions = self._reconstruct(action_id)
        if not revisions and action.get("outcome_observation") != previous:
            revisions = self._change(action_id, action["status"], action["verdict"],
                                     action["evidence"], action["alternatives"], force=True)
        return revisions

    def ingest(self, observation: Any) -> EvidenceUpdate:
        stamp = source_stamp(observation)
        if stamp is None:
            return EvidenceUpdate("unversioned")
        epoch, sequence = stamp
        revisions: list[dict] = []
        if epoch != self.epoch:
            if epoch in self.retired_epochs:
                return EvidenceUpdate("retired_epoch")
            if self.epoch is not None:
                if len(self.retired_epochs) >= 32:
                    return EvidenceUpdate("epoch_capacity")
                self.retired_epochs.add(self.epoch)
                for action_id, action in self.actions.items():
                    if action["verdict"] is None:
                        action["provisional"] = None
                        revisions.extend(self._change(action_id, "unconfirmed", None, [], []))
            self.epoch = epoch
            self.frames.clear()
            self.events.clear()
            self.frame_floor = self.event_floor = self.latest_sequence = 0
        if sequence <= self.frame_floor:
            return EvidenceUpdate("outside_window")
        projection = _projection(observation)
        callback_records = []
        raw_events = observation.get("action_evidence")
        if isinstance(raw_events, list):
            records = [(raw.get("channel"), raw) for raw in raw_events[-64:] if isinstance(raw, dict)]
        else:
            records = [(channel, observation.get(channel)) for channel in CHANNELS]
        for channel, raw in records:
            tool = CHANNELS.get(channel) if isinstance(channel, str) else None
            if tool is None or not isinstance(raw, dict):
                continue
            event_sequence = raw.get("source_sequence")
            action_id = raw.get("action_id")
            if (raw.get("source_epoch") != epoch or type(event_sequence) is not int
                    or event_sequence < 1 or not isinstance(action_id, str) or not action_id):
                continue
            watermark = observation["source_snapshot"].get("evidence_sequence")
            if type(watermark) is int and event_sequence > watermark:
                continue
            callback_records.append({
                "sequence": event_sequence, "action_id": action_id, "tool": tool,
                "stage": str(raw.get("stage") or "")[:80],
                "error": str(raw.get("error") or "")[:512],
            })
        frame = {"facts": projection, "events": callback_records}
        previous = self.frames.get(sequence)
        if previous is not None:
            if previous == frame:
                return EvidenceUpdate("duplicate")
            for event in callback_records:
                stored = self.events.get(event["sequence"])
                if stored is not None and any(stored.get(k) != v for k, v in event.items()):
                    stored["conflicted"] = True
                    revisions.extend(self._reconstruct(stored["action_id"]))
            return EvidenceUpdate("conflict", revisions=revisions)
        self.frames[sequence] = frame
        while len(self.frames) > self.frame_capacity:
            oldest = min(self.frames)
            self.frame_floor = max(self.frame_floor, oldest)
            del self.frames[oldest]
        adopt = sequence > self.latest_sequence
        self.latest_sequence = max(sequence, self.latest_sequence)
        for event in callback_records:
            key = event["sequence"]
            if key <= self.event_floor:
                continue
            existing = self.events.get(key)
            if existing is not None:
                original = {k: v for k, v in existing.items() if k not in ("conflicted", "other_actions")}
                if original != event:
                    existing["conflicted"] = True
                    revisions.extend(self._reconstruct(existing["action_id"]))
                    if event["action_id"] != existing["action_id"] and event["action_id"] in self.actions:
                        others = existing.setdefault("other_actions", [])
                        if event["action_id"] not in others:
                            others.append(event["action_id"])
                        revisions.extend(self._reconstruct(event["action_id"]))
                continue
            self.events[key] = event
            revisions.extend(self._reconstruct(event["action_id"]))
        while len(self.events) > self.event_capacity:
            oldest = min(self.events)
            self.event_floor = max(self.event_floor, oldest)
            del self.events[oldest]
        return EvidenceUpdate("current" if adopt else "late", adopt, revisions)

    def _reconstruct(self, action_id: str) -> list[dict]:
        action = self.actions.get(action_id)
        if action is None or action["epoch"] != self.epoch:
            return []
        candidates = [event for event in self.events.values()
                      if (event["action_id"] == action_id or action_id in event.get("other_actions", []))
                      and (event["tool"] == action["tool"]
                           or (event["tool"] == "fight.join" and action["tool"] == "farm.start"))]
        if not candidates:
            if action["evidence"]:
                return []
            verdict = action["provisional"]
            return self._change(action_id, "observed" if verdict else "unconfirmed", verdict, [], [])
        candidates.sort(key=lambda event: event["sequence"])
        last = candidates[-1]
        alternatives = sorted({"failed" if e["error"] or e["stage"] == "walk_stopped_short"
                               else "advanced" if e["stage"] == "walk_confirmed"
                               else "unconfirmed" for e in candidates})
        if last.get("conflicted"):
            status, verdict = "conflicted", None
        elif last["error"] or last["stage"] == "walk_stopped_short":
            status, verdict = "confirmed", "failed"
        elif last["tool"] == "nav.goto" and last["stage"] == "walk_confirmed":
            status, verdict = "confirmed", "advanced"
        else:
            verdict = action["provisional"]
            status = "observed" if verdict else "unconfirmed"
        action["error"] = (last["error"] or "the client stopped short of the requested cell") if verdict == "failed" else None
        return self._change(action_id, status, verdict, [last["sequence"]], alternatives)

    def _change(self, action_id: str, status: str, verdict: Optional[str],
                evidence: list[int], alternatives: list[str], force: bool = False) -> list[dict]:
        action = self.actions.get(action_id)
        if action is None:
            return []
        old = (action["status"], action["verdict"], action["evidence"], action["alternatives"])
        new = (status, verdict, evidence, alternatives)
        if old == new and not force:
            return []
        previous = action["verdict"]
        action.update(status=status, verdict=verdict, evidence=evidence, alternatives=alternatives,
                      revision=action["revision"] + 1)
        row = copy.deepcopy(action)
        row["previous_verdict"] = previous
        return [row]

    def belief(self, action_id: Optional[str]) -> Optional[dict]:
        action = self.actions.get(action_id)
        return copy.deepcopy(action) if action is not None else None

    def failure_count(self, context: tuple, tool: str, args: dict, prior: int = 0) -> int:
        count = prior
        signature = call_signature(tool, args)
        for action in self.actions.values():
            if (tuple(action["context"]) != tuple(context)
                    or call_signature(action["tool"], action["args"]) != signature):
                continue
            if action["verdict"] == "advanced":
                count = 0
            elif action["verdict"] == "failed":
                count += 1
        return count

    def describe(self, context: tuple, limit: int = 5) -> Optional[str]:
        rows = [row for row in self.actions.values() if tuple(row["context"]) == tuple(context)]
        if not rows:
            return None
        return "revisable evidence: " + "; ".join(
            "%s %s%s" % (label_call(row["tool"], row["args"]), row["status"],
                           ":" + row["verdict"] if row["verdict"] else "")
            for row in rows[-limit:])

    def dumps(self) -> str:
        return json.dumps({"epoch": self.epoch, "retired_epochs": sorted(self.retired_epochs),
                           "actions": list(self.actions.values()), "order": self._order,
                           "frames": self.frames, "events": self.events,
                           "frame_floor": self.frame_floor, "event_floor": self.event_floor,
                           "latest_sequence": self.latest_sequence}, ensure_ascii=False)

    def load(self, text: str) -> None:
        document = json.loads(text)
        if not isinstance(document, dict):
            raise ValueError("invalid evidence checkpoint")
        epoch = document.get("epoch")
        if epoch is not None and (not isinstance(epoch, str) or not epoch or len(epoch) > 128):
            raise ValueError("invalid evidence epoch")
        rows = document.get("actions")
        if not isinstance(rows, list) or len(rows) > self.action_capacity:
            raise ValueError("invalid evidence action window")
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("action_id"), str)
                    or not isinstance(row.get("args"), dict)
                    or not isinstance(row.get("context"), list)
                    or row.get("verdict") not in (None, "advanced", "failed")):
                raise ValueError("invalid evidence action")
        retired = document.get("retired_epochs", [])
        if not isinstance(retired, list) or len(retired) > 32 or not all(isinstance(e, str) for e in retired):
            raise ValueError("invalid evidence epochs")
        frames, events = document.get("frames", {}), document.get("events", {})
        if (not isinstance(frames, dict) or len(frames) > self.frame_capacity
                or not isinstance(events, dict) or len(events) > self.event_capacity):
            raise ValueError("invalid evidence history")
        for row in rows:
            if (not {"epoch", "tool", "order", "provisional", "status", "revision",
                     "evidence", "alternatives"}.issubset(row)
                    or not isinstance(row["epoch"], str) or not isinstance(row["tool"], str)
                    or type(row["order"]) is not int or type(row["revision"]) is not int
                    or row["provisional"] not in (None, "advanced")
                    or row["status"] not in ("pending", "observed", "confirmed", "conflicted", "unconfirmed")
                    or not isinstance(row["evidence"], list) or not isinstance(row["alternatives"], list)):
                raise ValueError("incomplete evidence action")
        if len({row["action_id"] for row in rows}) != len(rows):
            raise ValueError("duplicate evidence actions")
        parsed_frames = {int(k): v for k, v in frames.items()}
        parsed_events = {int(k): v for k, v in events.items()}
        for frame in parsed_frames.values():
            if not isinstance(frame, dict) or not isinstance(frame.get("facts"), dict) or not isinstance(frame.get("events"), list):
                raise ValueError("invalid evidence frame")
        for event in parsed_events.values():
            if (not isinstance(event, dict) or type(event.get("sequence")) is not int
                    or not isinstance(event.get("action_id"), str) or not isinstance(event.get("tool"), str)
                    or not isinstance(event.get("stage"), str) or not isinstance(event.get("error"), str)):
                raise ValueError("invalid evidence event")
        for key in ("order", "frame_floor", "event_floor", "latest_sequence"):
            if type(document.get(key, 0)) is not int or document.get(key, 0) < 0:
                raise ValueError("invalid evidence position")
        self.epoch = epoch
        self.retired_epochs = set(retired)
        self.actions = OrderedDict((row["action_id"], row) for row in rows)
        self._order = int(document.get("order", 0))
        self.frames = parsed_frames
        self.events = parsed_events
        self.frame_floor = int(document.get("frame_floor", 0))
        self.event_floor = int(document.get("event_floor", 0))
        self.latest_sequence = int(document.get("latest_sequence", 0))
