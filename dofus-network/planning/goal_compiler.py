"""Falsifiable Goal Compiler: compiles natural-language quest prose into Goal(item_id, qty).

Part of G126 (Decision Half closure):
1. Resolves natural-language quest prose and observations against `KnowledgeBase` (kb.py)
   and `QuestKnowledgeGraph` (quest_graph.py).
2. Enforces a two-way structural falsifier:
   (a) Structural Pre-condition: A candidate Goal(item_id, qty) is only verified and
       submitted to ScheduleManager if item_id is a verified edge in the active quest
       step's quest_graph requirements or its recipe DAG (transitive closure of ingredients).
   (b) Dynamic Runtime Falsifier: A submitted goal must confirm progress (inventory or
       quest-counter delta) within K steps, or ScheduleTap auto-aborts back to the base loop.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from navigation.quest_graph import QuestGuide, QuestKnowledgeGraph
from planning.kb import Item, KnowledgeBase
from planning.planner import Goal

log = logging.getLogger(__name__)


@dataclass
class GoalResolution:
    """The result of compiling quest prose into a verified Goal."""

    prose: str
    item_id: Optional[int] = None
    item_name: str = ""
    qty: int = 1
    quest_id: Optional[int] = None
    guide_id: Optional[str] = None
    step_num: Optional[int] = None
    verified: bool = False
    verification_reason: str = ""
    direct_requirement: bool = False
    recipe_dag_edge: bool = False
    recipe_root_id: Optional[int] = None
    allowed_item_ids: Set[int] = field(default_factory=set)

    @property
    def goal(self) -> Optional[Goal]:
        if self.verified and self.item_id is not None:
            return Goal(item_id=int(self.item_id), qty=max(1, int(self.qty)))
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prose": self.prose,
            "item_id": self.item_id,
            "item_name": self.item_name,
            "qty": self.qty,
            "quest_id": self.quest_id,
            "guide_id": self.guide_id,
            "step_num": self.step_num,
            "verified": self.verified,
            "verification_reason": self.verification_reason,
            "direct_requirement": self.direct_requirement,
            "recipe_dag_edge": self.recipe_dag_edge,
            "recipe_root_id": self.recipe_root_id,
        }


@dataclass
class ActiveGoalFalsifier:
    """Runtime tracking of a submitted goal to verify progress within K steps."""

    goal: Goal
    prose: str
    item_id: int
    target_qty: int
    quest_id: Optional[int]
    guide_id: Optional[str]
    submitted_at_step: int
    horizon_k: int = 5
    initial_inventory_qty: int = 0
    initial_quest_counter: int = 0
    confirmed: bool = False
    confirmed_at_step: Optional[int] = None
    confirmation_reason: str = ""
    falsified: bool = False
    aborted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": {"item_id": self.goal.item_id, "qty": self.goal.qty},
            "item_id": self.item_id,
            "target_qty": self.target_qty,
            "prose": self.prose,
            "quest_id": self.quest_id,
            "guide_id": self.guide_id,
            "submitted_at_step": self.submitted_at_step,
            "horizon_k": self.horizon_k,
            "initial_inventory_qty": self.initial_inventory_qty,
            "initial_quest_counter": self.initial_quest_counter,
            "confirmed": self.confirmed,
            "confirmed_at_step": self.confirmed_at_step,
            "confirmation_reason": self.confirmation_reason,
            "falsified": self.falsified,
            "aborted": self.aborted,
        }


def parse_requirement_item(text: str) -> List[Tuple[str, int]]:
    """Parse requirement strings like '2 Pointes de flèche' or 'Panoplie du Wa Wabbit (Couronne, Cape, Bâton)'."""
    text = text.strip()
    # Check for panoply / set with parts in parens, e.g. "Panoplie du Wa Wabbit (Couronne, Cape, Bâton)"
    m_set = re.match(r"(?:Panoplie|Set)\s+([^(]+)\s*\(([^)]+)\)", text, re.IGNORECASE)
    if m_set:
        base_name = m_set.group(1).strip()
        parts = [p.strip() for p in m_set.group(2).split(",") if p.strip()]
        out: List[Tuple[str, int]] = []
        for part in parts:
            full_name = f"{part} {base_name}".strip()
            out.append((full_name, 1))
        return out

    # Strip trailing parenthetical notes, e.g. "(acheté à l'hôtel des forgerons)"
    clean = re.sub(r"\s*\([^)]*\)", "", text).strip()
    # Match leading quantity, e.g. "2 Pointes de flèche"
    m_qty = re.match(r"^(\d+)\s*x?\s+(.+)$", clean)
    if m_qty:
        return [(m_qty.group(2).strip(), int(m_qty.group(1)))]
    return [(clean, 1)]


class GoalCompiler:
    """Compiles and structurally falsifies quest prose into verified planner Goals."""

    def __init__(self, kb: Optional[KnowledgeBase] = None,
                 quest_graph: Optional[QuestKnowledgeGraph] = None) -> None:
        self.kb = kb if kb is not None else KnowledgeBase()
        self.quest_graph = quest_graph if quest_graph is not None else QuestKnowledgeGraph()

    def resolve(self, prose: str, observation: Optional[Dict[str, Any]] = None) -> GoalResolution:
        """Resolve quest prose into a verified GoalResolution.

        Enforces Condition (a): structural verification against quest_graph requirements
        or recipe DAG.
        """
        obs = observation or {}
        prose_clean = (prose or "").strip()

        # 1. Identify the Quest Guide and Step
        guide_id = obs.get("guide_id")
        quest_id = obs.get("quest_id")
        step_num = obs.get("step") if obs.get("step") is not None else obs.get("step_num")

        guide: Optional[QuestGuide] = None
        if guide_id:
            guide = self.quest_graph.get_guide(str(guide_id))
        if guide is None and quest_id is not None:
            # Try searching by quest id string or quest name
            guide = self.quest_graph.get_guide(str(quest_id))

        if guide is None and prose_clean:
            # Search by prose matching guide names / terms
            guides = self.quest_graph.search_guides(prose_clean)
            if guides:
                guide = guides[0]
                guide_id = guide.id

        if quest_id is None and guide is None and prose_clean:
            # Try resolving quest via KB
            quest_ref = self.kb.resolve_quest_reference(prose_clean)
            if quest_ref is not None:
                quest_id = quest_ref

        # 2. Collect Structural Quest Requirements from QuestGraph
        req_items: Dict[int, int] = {}  # item_id -> required_qty
        if guide is not None:
            guide_id = guide.id
            raw_reqs = self.quest_graph.get_step_requirements(guide.id, step_num)
            for req_str in raw_reqs:
                for item_name_clean, qty in parse_requirement_item(req_str):
                    hits = self.kb.find_items(item_name_clean, limit=3)
                    if hits:
                        # Prefer exact name match or top hit
                        exact = next((h for h in hits if h.name.lower() == item_name_clean.lower()), hits[0])
                        req_items[exact.id] = max(req_items.get(exact.id, 0), qty)

        # 3. Build verified structural edge set V = Requirements + Recipe DAG Closure
        allowed_set: Set[int] = set(req_items.keys())
        recipe_roots: Dict[int, int] = {}  # ingredient_id -> root_requirement_id
        for root_id in list(req_items.keys()):
            closure = self.kb.recipe_closure(root_id, max_depth=10)
            for ing_id in closure:
                allowed_set.add(ing_id)
                if ing_id not in recipe_roots:
                    recipe_roots[ing_id] = root_id

        # 4. Extract Candidate Item and Quantity
        cand_id, cand_qty, cand_name = self._extract_candidate_item(prose_clean, obs, req_items, allowed_set)

        if cand_id is None:
            return GoalResolution(
                prose=prose_clean,
                quest_id=quest_id,
                guide_id=guide_id,
                step_num=step_num,
                verified=False,
                verification_reason="no_item_candidate_resolved_from_prose",
                allowed_item_ids=allowed_set,
            )

        # 5. Enforce Two-Way Structural Falsifier (Condition a)
        if not allowed_set:
            return GoalResolution(
                prose=prose_clean,
                item_id=cand_id,
                item_name=cand_name,
                qty=cand_qty,
                quest_id=quest_id,
                guide_id=guide_id,
                step_num=step_num,
                verified=False,
                verification_reason="no_structural_quest_graph_requirements_found",
                allowed_item_ids=allowed_set,
            )

        if cand_id in req_items:
            # Direct requirement edge
            return GoalResolution(
                prose=prose_clean,
                item_id=cand_id,
                item_name=cand_name,
                qty=cand_qty,
                quest_id=quest_id,
                guide_id=guide_id,
                step_num=step_num,
                verified=True,
                verification_reason="verified_direct_quest_requirement",
                direct_requirement=True,
                recipe_dag_edge=False,
                allowed_item_ids=allowed_set,
            )

        if cand_id in allowed_set:
            # Verified edge in recipe DAG
            root = recipe_roots.get(cand_id)
            return GoalResolution(
                prose=prose_clean,
                item_id=cand_id,
                item_name=cand_name,
                qty=cand_qty,
                quest_id=quest_id,
                guide_id=guide_id,
                step_num=step_num,
                verified=True,
                verification_reason=f"verified_recipe_dag_edge_for_requirement_{root}",
                direct_requirement=False,
                recipe_dag_edge=True,
                recipe_root_id=root,
                allowed_item_ids=allowed_set,
            )

        # Structural falsification: item is NOT an edge in requirements or recipe DAG
        return GoalResolution(
            prose=prose_clean,
            item_id=cand_id,
            item_name=cand_name,
            qty=cand_qty,
            quest_id=quest_id,
            guide_id=guide_id,
            step_num=step_num,
            verified=False,
            verification_reason=f"structural_falsification:item_{cand_id}_{cand_name}_not_in_quest_requirements_or_recipe_dag",
            allowed_item_ids=allowed_set,
        )

    def _extract_candidate_item(
        self,
        prose: str,
        obs: Dict[str, Any],
        req_items: Dict[int, int],
        allowed_set: Set[int],
    ) -> Tuple[Optional[int], int, str]:
        """Extract candidate (item_id, qty, name) from observation or prose."""
        # A. Explicit candidate in observation
        explicit_id = obs.get("candidate_item_id") or obs.get("item_id")
        if explicit_id is not None:
            try:
                eid = int(explicit_id)
                eqty = int(obs.get("qty") or obs.get("quantity") or req_items.get(eid, 1))
                it = self.kb.item(eid)
                name = it.name if it else str(eid)
                return eid, max(1, eqty), name
            except (ValueError, TypeError):
                pass

        if not prose:
            if len(req_items) == 1:
                rid, rqty = next(iter(req_items.items()))
                it = self.kb.item(rid)
                return rid, rqty, it.name if it else str(rid)
            return None, 1, ""

        # B. Check if prose explicitly mentions any of the allowed items (direct or recipe DAG)
        prose_lower = prose.lower()
        for allowed_id in allowed_set:
            it = self.kb.item(allowed_id)
            if it and it.name and it.name.lower() in prose_lower:
                qty = self._extract_qty_near(prose, it.name) or req_items.get(allowed_id, 1)
                return allowed_id, qty, it.name

        # C. Parse quantity + noun phrases from prose, and look up in KB
        # Regex patterns like "2 Pointes de flèche", "3 Cawotte", "1 Gros boulet"
        patterns = [
            r"(?:ramener|apporter|procurer|trouver|fabriquer|acheter|collecter|récupérer)\s+(\d+)\s+([a-zA-ZÀ-ÿ0-9'\s-]+)",
            r"(\d+)\s*x?\s+([a-zA-ZÀ-ÿ0-9'\s-]+)",
        ]
        for pat in patterns:
            for m in re.finditer(pat, prose, re.IGNORECASE):
                qty_val = int(m.group(1))
                item_text = m.group(2).strip()
                # Strip prepositions / trailing filler
                item_text = re.split(r"\b(?:pour|dans|avec|sur|de|du|d'|au)\b", item_text, maxsplit=1)[0].strip()
                if len(item_text) >= 3:
                    hits = self.kb.find_items(item_text, limit=3)
                    if hits:
                        return hits[0].id, qty_val, hits[0].name

        # D. General KB item search for phrases in prose
        words = prose_clean.split()
        for length in range(min(5, len(words)), 1, -1):
            for i in range(len(words) - length + 1):
                phrase = " ".join(words[i:i + length])
                if len(phrase) >= 4:
                    hits = self.kb.find_items(phrase, limit=1)
                    if hits and hits[0].name.lower() in prose_lower:
                        qty = self._extract_qty_near(prose, hits[0].name) or 1
                        return hits[0].id, qty, hits[0].name

        # E. Default to the primary quest requirement if available
        if req_items:
            rid, rqty = next(iter(req_items.items()))
            it = self.kb.item(rid)
            return rid, rqty, it.name if it else str(rid)

        return None, 1, ""

    @staticmethod
    def _extract_qty_near(text: str, item_name: str) -> Optional[int]:
        """Extract a number appearing directly before the item name in text."""
        escaped = re.escape(item_name)
        m = re.search(r"(\d+)\s*x?\s+" + escaped, text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        return None
