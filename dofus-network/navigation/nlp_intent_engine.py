"""Trilingual Natural Language Intent Engine for Dofus Spatial Navigation and Game Knowledge.

Supports French, English, and Spanish queries.
Seamlessly routes user/bot natural language requests to:
1. Coordinate pathfinding (DofusWorldGraph)
2. Zaap teleportation & fast-travel routing
3. Resource spot discovery & circular farm loops (ResourceKnowledgeGraph)
4. Quest walkthroughs & Island travel instructions (QuestKnowledgeGraph / Papycha)
5. General bot executive commands (Pass turn, Stop, Follow, Combat)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Tuple

from .world_graph import DofusWorldGraph
from .resource_graph import ResourceKnowledgeGraph
from .quest_graph import QuestKnowledgeGraph


class NLPIntent(str, Enum):
    NAVIGATE_COORDS = "NAVIGATE_COORDS"
    NAVIGATE_ZAAP = "NAVIGATE_ZAAP"
    FIND_RESOURCE = "FIND_RESOURCE"
    START_FARM_LOOP = "START_FARM_LOOP"
    QUEST_GUIDE = "QUEST_GUIDE"
    RESOURCE_LEVEL_REQ = "RESOURCE_LEVEL_REQ"
    COMBAT_ORDER = "COMBAT_ORDER"
    BOT_CONTROL = "BOT_CONTROL"
    UNKNOWN = "UNKNOWN"


@dataclass
class NLPQueryResult:
    raw_query: str
    detected_lang: str  # fr, en, es
    intent: NLPIntent
    confidence: float
    message_reply: str
    execution_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_query": self.raw_query,
            "detected_lang": self.detected_lang,
            "intent": self.intent.value,
            "confidence": round(self.confidence, 2),
            "message_reply": self.message_reply,
            "execution_payload": self.execution_payload,
        }


# Compiled once at import time instead of being re-parsed from source on
# every process_query()/detect_language() call (the re module caches recent
# patterns, but only up to _MAXCACHE=512 distinct patterns, which this file
# alone could evict under mixed traffic).
_RE_COORD_BRACKET = re.compile(r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]")
_RE_COORD_NATURAL = re.compile(r"\b(?:va\s+en|go\s+to|ve\s+a|aller\s+en|coords?|pos)\s+(-?\d+)[,\s]+(-?\d+)\b")
_RE_LANG_FR = re.compile(r"\b(le|la|les|du|des|un|une|va|en|comment|pour|au|dans|où|fer|blé|chêne|quête|donjon)\b")
_RE_LANG_ES = re.compile(r"\b(el|la|los|las|de|del|un|una|ve|a|cómo|para|en|dónde|hierro|trigo|roble|misión|mazmorra)\b")
_RE_LANG_EN = re.compile(r"\b(the|a|an|of|to|go|how|for|in|where|iron|wheat|oak|quest|dungeon|path)\b")


class DofusNLPEngine:
    """Natural Language Understanding pipeline for Dofus Touch."""

    def __init__(self) -> None:
        self.world_graph = DofusWorldGraph()
        self.resource_graph = ResourceKnowledgeGraph()
        self.quest_graph = QuestKnowledgeGraph()
        # Precomputed lowercase resource name aliases: the old code looped
        # over every resource and re-lowercased/re-stripped its name inside
        # process_query() on every call. The tuple order matches the original
        # (fr-stripped, en-stripped, es-stripped) so first-match behaviour is
        # identical.
        self._resource_aliases = [
            (
                r.name_fr.lower().replace("bois de ", "").replace("bois d'", ""),
                r.name_en.lower().replace(" wood", ""),
                r.name_es.lower().replace("madera de ", ""),
                r,
            )
            for r in self.resource_graph.resources
        ]
        # Zaap name variants used by the "zaap ..." intent, precomputed once.
        self._zaap_aliases = [
            (
                z.name_fr.lower().replace("village d'", "").replace("cité de ", "").replace("port de ", ""),
                z.name_en.lower(),
                z.name_es.lower(),
                z,
            )
            for z in self.world_graph.zaaps
        ]

    def detect_language(self, text: str) -> str:
        """Detects language between FR, EN, and ES using high-frequency lexical markers."""
        t = text.lower()
        fr_score = len(_RE_LANG_FR.findall(t))
        es_score = len(_RE_LANG_ES.findall(t))
        en_score = len(_RE_LANG_EN.findall(t))

        if es_score > fr_score and es_score > en_score:
            return "es"
        elif en_score > fr_score and en_score > es_score:
            return "en"
        return "fr"

    def process_query(
        self,
        query: str,
        current_pos: Tuple[int, int] = (4, -19),
    ) -> NLPQueryResult:
        """Processes an arbitrary natural language query in FR, EN, or ES."""
        q = query.strip()
        lang = self.detect_language(q)
        q_lower = q.lower()

        # 1. Coordinate Navigation: [X, Y] or "va en X, Y" / "go to X, Y" / "ve a X, Y"
        coord_match = _RE_COORD_BRACKET.search(q)
        if not coord_match:
            coord_match = _RE_COORD_NATURAL.search(q_lower)

        if coord_match:
            tx = int(coord_match.group(1))
            ty = int(coord_match.group(2))
            target = (tx, ty)
            path = self.world_graph.find_path(current_pos, target, allow_zaap=True, lang=lang)

            if lang == "en":
                reply = f"Route calculated from [{current_pos[0]}, {current_pos[1]}] to [{tx}, {ty}]: {path.total_steps} step(s) ({path.estimated_seconds:.1f}s), Zaap: {path.uses_zaap}."
            elif lang == "es":
                reply = f"Ruta calculada de [{current_pos[0]}, {current_pos[1]}] a [{tx}, {ty}]: {path.total_steps} paso(s) ({path.estimated_seconds:.1f}s), Zaap: {path.uses_zaap}."
            else:
                reply = f"Itinéraire calculé de [{current_pos[0]}, {current_pos[1]}] à [{tx}, {ty}]: {path.total_steps} étape(s) ({path.estimated_seconds:.1f}s), Zaap: {path.uses_zaap}."

            return NLPQueryResult(
                raw_query=query,
                detected_lang=lang,
                intent=NLPIntent.NAVIGATE_COORDS,
                confidence=0.98,
                message_reply=reply,
                execution_payload={"navigation_path": path.to_dict()},
            )

        # 2. Zaap Navigation: "zaap astrub", "aller au zaap de bonta", "teleport to village zaap"
        if "zaap" in q_lower or "téléporte" in q_lower or "teleport" in q_lower:
            for name_clean, name_en, name_es, z in self._zaap_aliases:
                if name_clean in q_lower or name_en in q_lower or name_es in q_lower:
                    path = self.world_graph.find_path(current_pos, (z.coord.x, z.coord.y), allow_zaap=True, lang=lang)
                    z_name = z.get_name(lang)
                    if lang == "en":
                        reply = f"Plotting navigation to Zaap '{z_name}' [{z.coord.x}, {z.coord.y}]."
                    elif lang == "es":
                        reply = f"Trazando navegación al Zaap '{z_name}' [{z.coord.x}, {z.coord.y}]."
                    else:
                        reply = f"Traçage de l'itinéraire vers le Zaap '{z_name}' [{z.coord.x}, {z.coord.y}]."

                    return NLPQueryResult(
                        raw_query=query,
                        detected_lang=lang,
                        intent=NLPIntent.NAVIGATE_ZAAP,
                        confidence=0.95,
                        message_reply=reply,
                        execution_payload={"zaap": z.get_name(lang), "navigation_path": path.to_dict()},
                    )

        # 3. Quest & Walkthrough Advice (Papycha source): "dofus cawotte", "comment aller sur otomai", "koulosse"
        if any(k in q_lower for k in ["quête", "quete", "quest", "misión", "mision", "dofus", "otomai", "otomaï", "moon", "frigost", "koulosse", "skeunk"]):
            matched_guides = self.quest_graph.search_guides(q_lower)
            if matched_guides:
                g = matched_guides[0]
                g_title = g.title_en if lang == "en" else (g.title_es if lang == "es" else g.title_fr)
                path_to_start = self.world_graph.find_path(current_pos, g.start_coord, allow_zaap=True, lang=lang)

                if lang == "en":
                    reply = (
                        f"📘 Guide '{g_title}' (Papycha verified):\n"
                        f"• Level required: {g.level_req}\n"
                        f"• Start NPC: {g.start_npc} in [{g.start_coord[0]}, {g.start_coord[1]}]\n"
                        f"• Items needed: {', '.join(g.required_items) if g.required_items else 'None'}\n"
                        f"• Key tip: {g.tips[0] if g.tips else 'Follow quest steps.'}"
                    )
                elif lang == "es":
                    reply = (
                        f"📘 Guía '{g_title}' (Verificada por Papycha):\n"
                        f"• Nivel requerido: {g.level_req}\n"
                        f"• PNJ inicial: {g.start_npc} en [{g.start_coord[0]}, {g.start_coord[1]}]\n"
                        f"• Objetos necesarios: {', '.join(g.required_items) if g.required_items else 'Ninguno'}\n"
                        f"• Consejo: {g.tips[0] if g.tips else 'Sigue los pasos.'}"
                    )
                else:
                    reply = (
                        f"📘 Guide '{g_title}' (Vérifié Papycha.fr):\n"
                        f"• Niveau requis: {g.level_req}\n"
                        f"• PNJ de départ: {g.start_npc} en [{g.start_coord[0]}, {g.start_coord[1]}]\n"
                        f"• Objets requis: {', '.join(g.required_items) if g.required_items else 'Aucun'}\n"
                        f"• Conseil clé: {g.tips[0] if g.tips else 'Suivre les étapes.'}"
                    )

                return NLPQueryResult(
                    raw_query=query,
                    detected_lang=lang,
                    intent=NLPIntent.QUEST_GUIDE,
                    confidence=0.96,
                    message_reply=reply,
                    execution_payload={
                        "guide": g.to_dict(),
                        "path_to_start_npc": path_to_start.to_dict(),
                    },
                )

        # 4. Resource Level Requirement: "quel niveau pour miner le kobalte", "what level to cut oak"
        if any(k in q_lower for k in ["niveau pour", "level for", "level to", "nivel para"]):
            for r_fr, r_en, r_es, r in self._resource_aliases:
                if r_fr in q_lower or r_en in q_lower or r_es in q_lower:
                    if lang == "en":
                        reply = f"Harvesting {r.name_en} requires {r.profession.capitalize()} Level {r.level_req}."
                    elif lang == "es":
                        reply = f"Recolectar {r.name_es} requiere {r.profession.capitalize()} Nivel {r.level_req}."
                    else:
                        reply = f"La récolte de '{r.name_fr}' requiert le métier de {r.profession.capitalize()} Niveau {r.level_req}."

                    return NLPQueryResult(
                        raw_query=query,
                        detected_lang=lang,
                        intent=NLPIntent.RESOURCE_LEVEL_REQ,
                        confidence=0.94,
                        message_reply=reply,
                        execution_payload={"resource_id": r.id, "level_req": r.level_req, "profession": r.profession},
                    )

        # 5. Start Circular Farm Loop: "farm du blé", "gather iron", "minar cobre", "récolte du chêne"
        if any(k in q_lower for k in ["farm", "récolte", "recolte", "gather", "harvest", "minar", "cosecha"]):
            for r_fr, r_en, r_es, r in self._resource_aliases:
                if r_fr in q_lower or r_en in q_lower or r_es in q_lower:
                    farm_loop = self.resource_graph.generate_farm_loop(r.name_fr, start_coord=current_pos, max_maps=6)
                    if farm_loop:
                        r_name = r.get_name(lang)
                        if lang == "en":
                            reply = f"🌾 Farm loop generated for {r_name}: {farm_loop.total_maps} maps circuit ({farm_loop.total_nodes_est} estimated nodes). Walking cycle ready."
                        elif lang == "es":
                            reply = f"🌾 Bucle de farmeo generado para {r_name}: circuito de {farm_loop.total_maps} mapas ({farm_loop.total_nodes_est} nodos estimados)."
                        else:
                            reply = f"🌾 Boucle de récolte générée pour '{r_name}': circuit de {farm_loop.total_maps} cartes (~{farm_loop.total_nodes_est} spots). Cycle prêt."

                        return NLPQueryResult(
                            raw_query=query,
                            detected_lang=lang,
                            intent=NLPIntent.START_FARM_LOOP,
                            confidence=0.95,
                            message_reply=reply,
                            execution_payload={"farm_loop": farm_loop.to_dict()},
                        )

        # 6. Find Resource Spots: "où trouver du fer", "where to find ash wood", "dónde hay trigo"
        if any(k in q_lower for k in ["où trouver", "ou trouver", "where", "dónde", "donde", "spot"]):
            for r_fr, r_en, r_es, r in self._resource_aliases:
                if r_fr in q_lower or r_en in q_lower or r_es in q_lower:
                    spots = self.resource_graph.search_spots(r.name_fr, near_pos=current_pos, max_results=4)
                    if spots:
                        r_name = r.get_name(lang)
                        top_spot = spots[0]
                        if lang == "en":
                            reply = f"📍 Found {len(spots)} dense spots for {r_name}. Best: [{top_spot.map_coord[0]}, {top_spot.map_coord[1]}] in {top_spot.subarea_name} ({top_spot.density} nodes)."
                        elif lang == "es":
                            reply = f"📍 Se encontraron {len(spots)} spots densos de {r_name}. Mejor: [{top_spot.map_coord[0]}, {top_spot.map_coord[1]}] en {top_spot.subarea_name} ({top_spot.density} nodos)."
                        else:
                            reply = f"📍 {len(spots)} spots denses trouvés pour '{r_name}'. Meilleur: [{top_spot.map_coord[0]}, {top_spot.map_coord[1]}] à {top_spot.subarea_name} ({top_spot.density} filons/arbres)."

                        return NLPQueryResult(
                            raw_query=query,
                            detected_lang=lang,
                            intent=NLPIntent.FIND_RESOURCE,
                            confidence=0.93,
                            message_reply=reply,
                            execution_payload={"spots": [s.to_dict() for s in spots]},
                        )

        # 7. Bot Executive Controls
        if any(k in q_lower for k in ["passe", "pass turn", "pasa turno"]):
            return NLPQueryResult(
                raw_query=query,
                detected_lang=lang,
                intent=NLPIntent.COMBAT_ORDER,
                confidence=0.99,
                message_reply="Passing turn." if lang == "en" else "Tour passé.",
                execution_payload={"action": "PASS_TURN"},
            )
        if any(k in q_lower for k in ["arrête", "arrete", "stop", "para"]):
            return NLPQueryResult(
                raw_query=query,
                detected_lang=lang,
                intent=NLPIntent.BOT_CONTROL,
                confidence=0.99,
                message_reply="Bot halted." if lang == "en" else "Bot arrêté.",
                execution_payload={"action": "STOP"},
            )
        if any(k in q_lower for k in ["suis-moi", "suis moi", "follow me", "sigueme", "sígueme"]):
            return NLPQueryResult(
                raw_query=query,
                detected_lang=lang,
                intent=NLPIntent.BOT_CONTROL,
                confidence=0.99,
                message_reply="Following leader." if lang == "en" else "Mode suivi activé.",
                execution_payload={"action": "FOLLOW"},
            )

        # Fallback / Unknown
        return NLPQueryResult(
            raw_query=query,
            detected_lang=lang,
            intent=NLPIntent.UNKNOWN,
            confidence=0.20,
            message_reply="Ordre non reconnu. Vous pouvez demander un déplacement [X, Y], un Zaap, un spot de récolte ou un guide de quête."
            if lang == "fr"
            else "Command not recognized. You can request navigation [X, Y], a Zaap, resource gathering, or a quest guide.",
            execution_payload={},
        )
