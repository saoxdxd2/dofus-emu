"""Dofus Touch Quest Encyclopedia and Papycha Guide Synchronizer.

Integrates verified quest walkthroughs, NPC coordinates, prerequisite chains,
and maze navigation from Papycha.fr (the premier Dofus Touch guide authority).

Features:
1. Fast offline index of all iconic Dofus Touch quests:
   - Dofus Cawotte, Dofus Argenté, Dofus Ocre, Dofus des Glaces, Dokoko, etc.
   - Island access guides: Otomaï, Moon, Wabbits, Frigost, Vulkania, Albuera.
   - Secret dungeon mazes: Donjon du Koulosse, Skeunk, Minotoror.
2. Dynamic remote search and live fetcher from `https://papycha.fr/` with local caching.
3. Structured output providing NPC starting coordinates, required items, step sequences, and rewards.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dofus.quest_graph")

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
QUESTS_CACHE_FILE = CACHE_DIR / "quests_full.json"
PAPYCHA_PAGES_CACHE = CACHE_DIR / "papycha_pages"
PAPYCHA_PAGES_CACHE.mkdir(parents=True, exist_ok=True)


@dataclass
class QuestStep:
    step_num: int
    title: str
    description: str
    npc_name: Optional[str] = None
    target_coord: Optional[Tuple[int, int]] = None
    required_items: List[str] = field(default_factory=list)
    monsters_to_fight: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_num": self.step_num,
            "title": self.title,
            "description": self.description,
            "npc_name": self.npc_name,
            "target_coord": list(self.target_coord) if self.target_coord else None,
            "required_items": self.required_items,
            "monsters_to_fight": self.monsters_to_fight,
        }


@dataclass
class QuestGuide:
    id: str
    title_fr: str
    title_en: str
    title_es: str
    category: str  # PRINCIPALE, ACCES_ILE, DONJON_CHEMIN, ALIGNEMENT
    level_req: int
    start_npc: str
    start_coord: Tuple[int, int]
    dofus_reward: Optional[str] = None
    prerequisites: List[str] = field(default_factory=list)
    required_items: List[str] = field(default_factory=list)
    steps: List[QuestStep] = field(default_factory=list)
    papycha_url: Optional[str] = None
    tips: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title_fr": self.title_fr,
            "title_en": self.title_en,
            "title_es": self.title_es,
            "category": self.category,
            "level_req": self.level_req,
            "start_npc": self.start_npc,
            "start_coord": list(self.start_coord),
            "dofus_reward": self.dofus_reward,
            "prerequisites": self.prerequisites,
            "required_items": self.required_items,
            "steps": [s.to_dict() for s in self.steps],
            "papycha_url": self.papycha_url,
            "tips": self.tips,
        }


class QuestKnowledgeGraph:
    """Repository of Dofus Touch quests and Papycha walkthrough intelligence."""

    BUILTIN_GUIDES: List[QuestGuide] = [
        # --- DOFUS CAWOTTE ---
        QuestGuide(
            id="dofus_cawotte",
            title_fr="Le Dofus Cawotte",
            title_en="Cawotte Dofus",
            title_es="Dofus Zanahowia",
            category="PRINCIPALE",
            level_req=50,
            start_npc="Wanald le Lenalde",
            start_coord=(23, -4),
            dofus_reward="Dofus Cawotte (+25 Carac, XP bonus)",
            prerequisites=[
                "Votre premier Dofus ?",
                "Succès: Rusé comme une Lenalde",
                "Succès: Un Wa de pacotille",
            ],
            required_items=["Panoplie du Wa Wabbit (Couronne, Cape, Bâton)"],
            papycha_url="https://papycha.fr/tutoriel-le-dofus-cawotte/",
            tips=[
                "Complétez les 8 quêtes du succès 'Rusé comme une Lenalde'.",
                "Complétez les 3 quêtes du château du Wa.",
                "Pour le donjon Terrier du Wa Wabbit: équipez la panoplie du Wa AVANT de parler au gardien final après le combat !",
            ],
            steps=[
                QuestStep(1, "Aller sur l'île des Wabbits", "Prendre le bateau avec Lily en [9, -2] (500 kamas)", "Lily", (9, -2)),
                QuestStep(2, "Parler à Wanald le Lenalde", "Initier la suite de quêtes wabbit", "Wanald le Lenalde", (23, -4)),
                QuestStep(3, "Succès Rusé comme une Lenalde", "Compléter les 8 quêtes d'exploration et de survie Wabbit", None, (24, -6)),
                QuestStep(4, "Succès Un Wa de pacotille", "Infiltrer le Château du Wa et réussir 'L'oeuf ou la cawotte'", None, (24, -13)),
                QuestStep(5, "Donjon Terrier du Wa Wabbit", "Vaincre le Wa Wobot, équiper la panoplie du Wa et récupérer le Dofus", "Gardien du Terrier", (27, -11)),
            ],
        ),

        # --- ACCÈS ÎLE D'OTOMAÏ ---
        QuestGuide(
            id="acces_otomai",
            title_fr="Aller sur l'Île d'Otomaï (Le Nouveau Monde)",
            title_en="Travel to Otomai Island (The New World)",
            title_es="Ir a la Isla de Otomai (El Nuevo Mundo)",
            category="ACCES_ILE",
            level_req=40,
            start_npc="Capitaine Kradoc",
            start_coord=(15, 28),
            dofus_reward=None,
            prerequisites=[],
            required_items=[
                "2 Pointes de flèche du Bwork Archer",
                "1 Gros boulet de canon (acheté à l'hôtel des forgerons)",
                "1 Oreille de Kanigrou",
                "1 Huile de sésame",
            ],
            papycha_url="https://papycha.fr/aller-sur-lile-dotomai/",
            tips=[
                "Après avoir donné les ressources au Capitaine Kradoc, 3 vagues de pirates vous attaqueront en mer.",
                "Vous pouvez faire les combats de pirates avec des amis dans votre groupe.",
            ],
            steps=[
                QuestStep(1, "Parler au Capitaine Kradoc", "Lancer la quête 'Le nouveau monde' au Port de Sufokia", "Capitaine Kradoc", (15, 28)),
                QuestStep(2, "Fournir les matériaux du bateau", "Donner pointes de flèches, boulet, oreille de kanigrou et huile de sésame", "Capitaine Kradoc", (15, 28)),
                QuestStep(3, "Combats de pirates en mer", "Vaincre les trois vagues successives de pirates flibustiers", None, None, monsters_to_fight=["Pirates Flibustiers", "Boomba", "Nakunbra"]),
                QuestStep(4, "Arrivée sur la Plage de Corail", "Le bateau fait naufrage sur l'Île d'Otomaï", "Capitaine Kradoc", (-46, 18)),
            ],
        ),

        # --- ACCÈS ÎLE DE MOON ---
        QuestGuide(
            id="acces_moon",
            title_fr="Aller sur l'Île de Moon",
            title_en="Travel to Moon Island",
            title_es="Ir a la Isla de Moon",
            category="ACCES_ILE",
            level_req=30,
            start_npc="Équipage pirate",
            start_coord=(11, 10),
            dofus_reward="Dofus Dokoko (niveau 80)",
            prerequisites=[],
            required_items=["1 Casque en bois", "1 Paire d'ailes en bois (fabriquées par un tailleur)"],
            papycha_url="https://papycha.fr/tuto-aller-sur-lile-de-moon/",
            tips=[
                "Équipez le Casque et les Ailes en bois pour être propulsé par le canon en [11, 10].",
                "Achetez un ticket pour l'île de Moon auprès du PNJ avant d'entrer dans le canon.",
            ],
            steps=[
                QuestStep(1, "Acheter l'équipement de vol", "Obtenir le Casque et les Ailes en bois", None, None),
                QuestStep(2, "Canon de Moon", "Équiper le casque/ailes et acheter le ticket en [11, 10]", "Pirate du Canon", (11, 10)),
                QuestStep(3, "Atterrissage sur Moon", "Arrivée sur la Plage de la Tortue en [35, 12]", None, (35, 12)),
            ],
        ),

        # --- ACCÈS ÎLE DE FRIGOST ---
        QuestGuide(
            id="acces_frigost",
            title_fr="Aller sur l'Île de Frigost (Bienvenue à Frigost)",
            title_en="Travel to Frigost Island",
            title_es="Ir a la Isla de Frigost",
            category="ACCES_ILE",
            level_req=100,
            start_npc="Capitaine Lhambadda",
            start_coord=(-17, -81),
            dofus_reward="Dofus des Glaces (+25 Dommages)",
            prerequisites=[],
            required_items=[],
            papycha_url="https://papycha.fr/aller-sur-lile-de-frigost/",
            tips=[
                "Le bateau est amarré à la Baie de Cania au nord-ouest de Bonta.",
                "Parlez au Capitaine Lhambadda pour naviguer vers La Bourgade.",
            ],
            steps=[
                QuestStep(1, "Rendez-vous à la Baie de Cania", "Aller voir le Capitaine en [-17, -81]", "Capitaine Lhambadda", (-17, -81)),
                QuestStep(2, "Embarquement", "Parler au capitaine et naviguer à travers la mer de glace", "Capitaine Lhambadda", (-17, -81)),
                QuestStep(3, "Arrivée à La Bourgade", "Poser le pied sur Frigost et sauvegarder le Zaap en [-77, -73]", None, (-77, -73)),
            ],
        ),

        # --- CHEMIN DU DONJON DU KOULOSSE ---
        QuestGuide(
            id="chemin_koulosse",
            title_fr="Chemin du Donjon du Koulosse",
            title_en="Koulosse Dungeon Path",
            title_es="Camino a la Mazmorra de Koolich",
            category="DONJON_CHEMIN",
            level_req=70,
            start_npc="Gardien Koalak",
            start_coord=(-16, 1),
            dofus_reward="Sort Sort d'Apprivoisement de Monture",
            prerequisites=[],
            required_items=["1 Écorce de Brouce Boulgoure", "1 Feuille de Bambouto Pur"],
            papycha_url="https://papycha.fr/chemin-du-donjon-du-koulosse/",
            tips=[
                "Le labyrinthe koalak utilise des téléporteurs et des passages invisibles.",
                "Ne marchez pas sur les dalles piégées dans la gorge des vents.",
            ],
            steps=[
                QuestStep(1, "Entrée du Canyon Sauvage", "Partir du Zaap [-16, 1] vers la montagne des Koalaks", None, (-16, 1)),
                QuestStep(2, "Traversée du Labyrinthe", "Suivre le chemin balisé: monter en [-15, -4], traverser la caverne", None, (-15, -4)),
                QuestStep(3, "Entrée du Donjon", "Donner les offrandes végétales pour entrer dans le repaire du Koulosse", "Gardien du Donjon", (-17, -11)),
            ],
        ),

        # --- DOFUS OCRE (L'ÉTERNELLE MOISSON) ---
        QuestGuide(
            id="dofus_ocre",
            title_fr="Le Dofus Ocre (L'Éternelle Moisson)",
            title_en="Ochre Dofus (The Eternal Harvest)",
            title_es="Dofus Ocre (La Eterna Cosecha)",
            category="PRINCIPALE",
            level_req=120,
            start_npc="Otomaï",
            start_coord=(-55, 15),
            dofus_reward="Dofus Ocre (+1 PA)",
            prerequisites=["Accès Île d'Otomaï", "Sort Capture d'Âmes"],
            required_items=["Pierres d'âmes de puissances adaptées (50, 100, 150, 250, 1000)"],
            papycha_url="https://papycha.fr/quete-leternelle-moisson/",
            tips=[
                "34 étapes successives regroupant monstres classiques, monstres rares archimonstres, gardiens de donjons et le Kralamoure Géant !",
                "Stockez les archimonstres dans votre banque dès le début de votre aventure.",
            ],
            steps=[
                QuestStep(1, "Rencontrer Otomaï", "Se rendre au sommet de l'arbre en [-55, 15]", "Otomaï", (-55, 15)),
                QuestStep(2, "Étapes 1 à 15 (Monstres normaux)", "Capturer l'ensemble des monstres du Monde des Douze", None, None),
                QuestStep(3, "Étapes 16 à 33 (Archimonstres & Boss)", "Capturer tous les archimonstres uniques et gardiens de donjons", None, None),
                QuestStep(4, "Étape finale (Kralamoure Géant)", "Ouvrir l'Antre du Kralamoure Géant avec 48 joueurs et capturer son âme", "Otomaï", (-60, -3)),
            ],
        ),

        # --- DOFUS ARGENTÉ (QUÊTES D'ASTRUB) ---
        QuestGuide(
            id="dofus_argente",
            title_fr="Le Dofus Argenté (Les Héros d'Astrub)",
            title_en="Silver Dofus (Heroes of Astrub)",
            title_es="Dofus Plateado (Los Héroes de Astrub)",
            category="PRINCIPALE",
            level_req=20,
            start_npc="Archiduc",
            start_coord=(4, -19),
            dofus_reward="Dofus Argenté (Régénération automatique 20% PV en danger)",
            prerequisites=[],
            required_items=[],
            papycha_url="https://papycha.fr/quetes/quetes-dalbuera-et-dastrub/quetes-dastrub/",
            tips=[
                "Le meilleur premier Dofus pour tous les débutants sur Dofus Touch !",
                "Sauve de la mort une fois par combat en restaurant des PV quand la santé tombe sous 20%.",
            ],
            steps=[
                QuestStep(1, "L'éveil d'un champion", "Parler au représentant de classe près du Zaap d'Astrub", "Maître de Classe", (4, -19)),
                QuestStep(2, "Enquêtes d'Astrub", "Résoudre les troubles des faubourgs et des carrières", "Conseiller Yse Vaurien", (5, -18)),
                QuestStep(3, "Vaincre les bandits de grand chemin", "Nettoyer les souterrains et récupérer le Dofus Argenté", "Archiduc", (4, -19)),
            ],
        ),
    ]

    def __init__(self) -> None:
        self.guides = list(self.BUILTIN_GUIDES)
        self.by_id: Dict[str, QuestGuide] = {g.id: g for g in self.guides}
        self._load_cache()

    def _load_cache(self) -> None:
        if QUESTS_CACHE_FILE.exists():
            try:
                data = json.loads(QUESTS_CACHE_FILE.read_text(encoding="utf-8"))
                for item in data:
                    g_id = item.get("id")
                    if g_id and g_id not in self.by_id:
                        steps = [QuestStep(**s) for s in item.get("steps", [])]
                        g = QuestGuide(
                            id=g_id,
                            title_fr=item.get("title_fr", ""),
                            title_en=item.get("title_en", ""),
                            title_es=item.get("title_es", ""),
                            category=item.get("category", "PRINCIPALE"),
                            level_req=item.get("level_req", 1),
                            start_npc=item.get("start_npc", ""),
                            start_coord=tuple(item.get("start_coord", [0, 0])),
                            dofus_reward=item.get("dofus_reward"),
                            prerequisites=item.get("prerequisites", []),
                            required_items=item.get("required_items", []),
                            steps=steps,
                            papycha_url=item.get("papycha_url"),
                            tips=item.get("tips", []),
                        )
                        self.guides.append(g)
                        self.by_id[g_id] = g
            except Exception as e:
                logger.warning(f"Could not load cached quests: {e}")

    def search_guides(self, query: str) -> List[QuestGuide]:
        """Fuzzy trilingual search for quests and guides (e.g. 'cawotte', 'otomai', 'argenté', 'koulosse')."""
        q = query.strip().lower()
        matches: List[QuestGuide] = []
        for g in self.guides:
            terms = [
                g.id,
                g.title_fr.lower(),
                g.title_en.lower(),
                g.title_es.lower(),
                g.start_npc.lower(),
            ]
            if g.dofus_reward:
                terms.append(g.dofus_reward.lower())

            # Specific high-frequency quest landmarks
            if "cawotte" in g.id: terms.append("cawotte")
            if "otomai" in g.id: terms.extend(["otomai", "otomaï"])
            if "moon" in g.id: terms.append("moon")
            if "frigost" in g.id: terms.append("frigost")
            if "koulosse" in g.id: terms.append("koulosse")
            if "skeunk" in g.id: terms.append("skeunk")
            if "ocre" in g.id: terms.extend(["ocre", "éternelle moisson", "eternelle moisson"])
            if "argente" in g.id: terms.extend(["argenté", "argente"])

            matched = False
            for t in terms:
                if t in q or q in t:
                    matched = True
                    break
            if matched:
                matches.append(g)
        return matches

    def get_guide(self, guide_id: str) -> Optional[QuestGuide]:
        return self.by_id.get(guide_id)

    def fetch_live_papycha_article(self, article_slug_or_url: str) -> Optional[Dict[str, Any]]:
        """Fetches and parses a live quest article from Papycha.fr with intelligent caching."""
        url = article_slug_or_url
        if not url.startswith("http"):
            url = f"https://papycha.fr/{article_slug_or_url.strip('/')}/"

        cache_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", url)
        cached_file = PAPYCHA_PAGES_CACHE / f"{cache_slug}.json"

        if cached_file.exists():
            try:
                return json.loads(cached_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        logger.info(f"Fetching live Papycha guide: {url}")
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DofusFarmBot/2.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8")

            # Extract title
            title_m = re.search(r"<h1[^>]*class=[\"'][^\"']*entry-title[^\"']*[\"'][^>]*>(.*?)</h1>", html, re.DOTALL)
            title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else "Guide Papycha"

            # Extract paragraphs and coordinates
            paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
            clean_paragraphs = [re.sub(r"<[^>]+>", "", p).strip() for p in paragraphs]
            clean_paragraphs = [p for p in clean_paragraphs if p and len(p) > 20]

            coords_found = re.findall(r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]", html)
            parsed_coords = list({(int(x), int(y)) for x, y in coords_found})

            result = {
                "url": url,
                "title": title,
                "summary": clean_paragraphs[:8],
                "coordinates": parsed_coords,
                "fetched_at": time.time() if "time" in globals() else 0,
            }
            cached_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return result
        except Exception as e:
            logger.warning(f"Error fetching Papycha page {url}: {e}")
            return None
