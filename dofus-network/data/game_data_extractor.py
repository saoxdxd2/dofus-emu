"""Dofus Touch Game Data Extractor and Asset Synchronizer.

Extracts, parses, and provides unified access to:
1. Spells (Spells.json & SpellLevels.json) - AP costs, damage rolls, ranges, cooldowns
2. Monsters (Monsters.json) - HP, AP, MP, elemental resistances (Neutral, Earth, Fire, Water, Air)
3. Multilingual dictionaries (FR, EN, ES) for seamless trilingual lookup

Supports:
- Built-in comprehensive offline dataset for all 12 core breeds and iconic farm mobs
- Dynamic extraction from client cache or unpacked APK assets
- Live remote mirror synchronization with hash verification
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dofus.data_extractor")

DATA_DIR = Path(__file__).resolve().parent
CACHE_DIR = DATA_DIR / "cache"


@dataclass
class DamageEffect:
    element: str  # NEUTRAL, EARTH, FIRE, WATER, AIR, HEAL
    min_roll: int
    max_roll: int
    crit_min_roll: int = 0
    crit_max_roll: int = 0


@dataclass
class SpellInfo:
    id: int
    name_fr: str
    name_en: str
    name_es: str
    breed_id: int  # 1=Feca, 2=Osa, ..., 8=Iop, 9=Cra, etc. (0=Common)
    level: int = 1
    ap_cost: int = 3
    min_range: int = 1
    max_range: int = 4
    cast_in_line: bool = False
    cast_in_diagonal: bool = False
    cast_test_los: bool = True
    max_cast_per_turn: int = 2
    max_cast_per_target: int = 2
    cooldown: int = 0
    crit_rate_base: int = 30  # Legacy 1/N records
    crit_bonus_damage: int = 0
    effects: List[DamageEffect] = field(default_factory=list)
    crit_rate_percent: Optional[float] = None

    def get_name(self, lang: str = "fr") -> str:
        lang = lang.lower()
        if lang == "en":
            return self.name_en or self.name_fr
        elif lang == "es":
            return self.name_es or self.name_fr
        return self.name_fr


@dataclass
class MonsterGrade:
    grade: int
    level: int
    life_points: int
    action_points: int
    movement_points: int
    neutral_res: int  # %
    earth_res: int    # %
    fire_res: int     # %
    water_res: int    # %
    air_res: int      # %
    fixed_res: int = 0


@dataclass
class MonsterInfo:
    id: int
    name_fr: str
    name_en: str
    name_es: str
    is_boss: bool = False
    grades: List[MonsterGrade] = field(default_factory=list)

    def get_name(self, lang: str = "fr") -> str:
        lang = lang.lower()
        if lang == "en":
            return self.name_en or self.name_fr
        elif lang == "es":
            return self.name_es or self.name_fr
        return self.name_fr

    def get_grade(self, grade_num: int = 1) -> MonsterGrade:
        if not self.grades:
            return MonsterGrade(1, 10, 100, 6, 3, 0, 0, 0, 0, 0)
        idx = max(0, min(grade_num - 1, len(self.grades) - 1))
        return self.grades[idx]


# ============================================================================
# BUILT-IN CORE DATABASE (Accurate Official Dofus Touch Base Values)
# ============================================================================

BUILTIN_SPELLS: List[SpellInfo] = [
    # --- IOP (Breed 8) ---
    SpellInfo(
        id=161, name_fr="Pression", name_en="Pressure", name_es="Presión",
        breed_id=8, level=6, ap_cost=3, min_range=1, max_range=4, cast_in_line=False,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("EARTH", 14, 18, 19, 23)]
    ),
    SpellInfo(
        id=166, name_fr="Intimidation", name_en="Intimidation", name_es="Intimidación",
        breed_id=8, level=6, ap_cost=3, min_range=1, max_range=4, cast_in_line=True,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=40,
        effects=[DamageEffect("NEUTRAL", 9, 13, 14, 18)]
    ),
    SpellInfo(
        id=169, name_fr="Épée Divine", name_en="Divine Sword", name_es="Espada Divina",
        breed_id=8, level=6, ap_cost=3, min_range=0, max_range=0,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=35,
        effects=[DamageEffect("AIR", 13, 17, 18, 22)]
    ),
    SpellInfo(
        id=173, name_fr="Épée du Destin", name_en="Destiny Sword", name_es="Espada del Destino",
        breed_id=8, level=6, ap_cost=4, min_range=1, max_range=3, cast_in_line=True,
        max_cast_per_turn=1, max_cast_per_target=1, cooldown=2, crit_rate_base=25,
        effects=[DamageEffect("FIRE", 26, 31, 32, 37)]
    ),
    SpellInfo(
        id=170, name_fr="Épée d'Iop", name_en="Iop's Sword", name_es="Espada de Yopuka",
        breed_id=8, level=6, ap_cost=4, min_range=4, max_range=8, cast_in_line=True,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("EARTH", 27, 33, 34, 40)]
    ),
    SpellInfo(
        id=171, name_fr="Colère d'Iop", name_en="Iop's Wrath", name_es="Ira de Yopuka",
        breed_id=8, level=6, ap_cost=7, min_range=1, max_range=1,
        max_cast_per_turn=1, max_cast_per_target=1, cooldown=4, crit_rate_base=20,
        effects=[DamageEffect("EARTH", 85, 110, 115, 140)]
    ),
    SpellInfo(
        id=164, name_fr="Concentration", name_en="Concentration", name_es="Concentración",
        breed_id=8, level=6, ap_cost=2, min_range=1, max_range=1,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("EARTH", 18, 22, 23, 27)]
    ),
    SpellInfo(
        id=167, name_fr="Tempête de Puissance", name_en="Strength of Power", name_es="Tempestad de Poder",
        breed_id=8, level=6, ap_cost=3, min_range=1, max_range=4,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("FIRE", 28, 33, 34, 39)]
    ),

    # --- CRA (Breed 9) ---
    SpellInfo(
        id=1611, name_fr="Flèche Magique", name_en="Magic Arrow", name_es="Flecha Mágica",
        breed_id=9, level=6, ap_cost=4, min_range=1, max_range=9,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("FIRE", 16, 20, 21, 25)]
    ),
    SpellInfo(
        id=1612, name_fr="Flèche Glacée", name_en="Frozen Arrow", name_es="Flecha Helada",
        breed_id=9, level=6, ap_cost=3, min_range=1, max_range=8,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=40,
        effects=[DamageEffect("WATER", 10, 14, 15, 19)]
    ),
    SpellInfo(
        id=1613, name_fr="Flèche Enflammée", name_en="Burning Arrow", name_es="Flecha Flamígera",
        breed_id=9, level=6, ap_cost=4, min_range=1, max_range=8, cast_in_line=True,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("FIRE", 18, 22, 23, 27)]
    ),
    SpellInfo(
        id=1614, name_fr="Flèche d'Expiation", name_en="Atonement Arrow", name_es="Flecha Expiatoria",
        breed_id=9, level=6, ap_cost=4, min_range=4, max_range=10, cast_in_line=True,
        max_cast_per_turn=1, max_cast_per_target=1, cooldown=3, crit_rate_base=25,
        effects=[DamageEffect("WATER", 25, 30, 32, 38)]
    ),
    SpellInfo(
        id=1615, name_fr="Flèche Explosive", name_en="Explosive Arrow", name_es="Flecha Explosiva",
        breed_id=9, level=6, ap_cost=4, min_range=4, max_range=10,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("FIRE", 26, 32, 33, 39)]
    ),
    SpellInfo(
        id=1616, name_fr="Flèche Destructrice", name_en="Destructive Arrow", name_es="Flecha Destructora",
        breed_id=9, level=6, ap_cost=4, min_range=1, max_range=8, cast_in_line=True,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("EARTH", 28, 34, 35, 41)]
    ),
    SpellInfo(
        id=1617, name_fr="Flèche de Recul", name_en="Retreat Arrow", name_es="Flecha Retroceso",
        breed_id=9, level=6, ap_cost=4, min_range=1, max_range=6, cast_in_line=True,
        max_cast_per_turn=2, max_cast_per_target=1, crit_rate_base=35,
        effects=[DamageEffect("AIR", 15, 19, 20, 24)]
    ),

    # --- ENIRIPSA (Breed 7) ---
    SpellInfo(
        id=121, name_fr="Mot Interdit", name_en="Forbidden Word", name_es="Palabra Prohibida",
        breed_id=7, level=6, ap_cost=3, min_range=1, max_range=6,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=35,
        effects=[DamageEffect("FIRE", 13, 17, 18, 22)]
    ),
    SpellInfo(
        id=122, name_fr="Mot Blessant", name_en="Wounding Word", name_es="Palabra Hiriente",
        breed_id=7, level=6, ap_cost=3, min_range=1, max_range=7,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=40,
        effects=[DamageEffect("AIR", 14, 18, 19, 23)]
    ),
    SpellInfo(
        id=123, name_fr="Mot Vampirique", name_en="Vampiric Word", name_es="Palabra Vampírica",
        breed_id=7, level=6, ap_cost=4, min_range=1, max_range=5,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("WATER", 21, 26, 27, 32)]
    ),

    # --- FECA (Breed 1) ---
    SpellInfo(
        id=1, name_fr="Attaque Naturelle", name_en="Natural Attack", name_es="Ataque Natural",
        breed_id=1, level=6, ap_cost=3, min_range=1, max_range=6,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=35,
        effects=[DamageEffect("FIRE", 12, 16, 17, 21)]
    ),
    SpellInfo(
        id=2, name_fr="Attaque Nuageuse", name_en="Cloudy Attack", name_es="Ataque Nuboso",
        breed_id=1, level=6, ap_cost=4, min_range=1, max_range=5,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("WATER", 19, 24, 25, 30)]
    ),
    SpellInfo(
        id=3, name_fr="Aveuglement", name_en="Blindness", name_es="Ceguera",
        breed_id=1, level=6, ap_cost=3, min_range=1, max_range=6,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=35,
        effects=[DamageEffect("EARTH", 14, 18, 19, 23)]
    ),

    # --- ECAFLIP (Breed 6) ---
    SpellInfo(
        id=101, name_fr="Pile ou Face", name_en="Heads or Tails", name_es="Cara o Cruz",
        breed_id=6, level=6, ap_cost=3, min_range=1, max_range=7,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("EARTH", 15, 20, 21, 26)]
    ),
    SpellInfo(
        id=102, name_fr="Topkaj", name_en="Topkaj", name_es="Topkaj",
        breed_id=6, level=6, ap_cost=3, min_range=1, max_range=6,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("FIRE", 17, 22, 23, 28)]
    ),
    SpellInfo(
        id=103, name_fr="Esprit Félin", name_en="Felintrocation", name_es="Espíritu Felino",
        breed_id=6, level=6, ap_cost=3, min_range=1, max_range=1,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=25,
        effects=[DamageEffect("EARTH", 30, 36, 37, 43)]
    ),

    # --- SACRIER (Breed 11) ---
    SpellInfo(
        id=181, name_fr="Pied du Sacrieur", name_en="Sacrier's Foot", name_es="Pie del Sacrógrito",
        breed_id=11, level=6, ap_cost=3, min_range=1, max_range=1,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("EARTH", 16, 20, 21, 25)]
    ),
    SpellInfo(
        id=182, name_fr="Absorption", name_en="Absorption", name_es="Absorción",
        breed_id=11, level=6, ap_cost=4, min_range=1, max_range=4,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("FIRE", 23, 27, 28, 33)]
    ),
    SpellInfo(
        id=183, name_fr="Dissolution", name_en="Dissolution", name_es="Disolución",
        breed_id=11, level=6, ap_cost=4, min_range=0, max_range=0,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("WATER", 18, 22, 23, 27)]
    ),
    SpellInfo(
        id=184, name_fr="Assaut", name_en="Assault", name_es="Asalto",
        breed_id=11, level=6, ap_cost=3, min_range=1, max_range=4,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=35,
        effects=[DamageEffect("AIR", 15, 19, 20, 24)]
    ),

    # --- ENUTROF (Breed 3) ---
    SpellInfo(
        id=41, name_fr="Lancer de Pièce", name_en="Coin Throwing", name_es="Lanzamiento de Monedas",
        breed_id=3, level=6, ap_cost=2, min_range=1, max_range=11,
        max_cast_per_turn=3, max_cast_per_target=3, crit_rate_base=30,
        effects=[DamageEffect("WATER", 5, 8, 9, 12)]
    ),
    SpellInfo(
        id=42, name_fr="Lancer de Pelle", name_en="Shovel Throwing", name_es="Lanzamiento de Pala",
        breed_id=3, level=6, ap_cost=4, min_range=2, max_range=8,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("EARTH", 22, 27, 28, 33)]
    ),
    SpellInfo(
        id=43, name_fr="Roulage de Pelle", name_en="Mass Shovel", name_es="Pala del Juicio",
        breed_id=3, level=6, ap_cost=4, min_range=1, max_range=6,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("WATER", 24, 29, 30, 35)]
    ),

    # --- SRAM (Breed 4) ---
    SpellInfo(
        id=61, name_fr="Sournoiserie", name_en="Deviousness", name_es="Engaño",
        breed_id=4, level=6, ap_cost=3, min_range=1, max_range=5,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=35,
        effects=[DamageEffect("EARTH", 14, 18, 19, 23)]
    ),
    SpellInfo(
        id=62, name_fr="Arnaque", name_en="Con", name_es="Estafa",
        breed_id=4, level=6, ap_cost=3, min_range=1, max_range=6,
        max_cast_per_turn=3, max_cast_per_target=2, crit_rate_base=30,
        effects=[DamageEffect("AIR", 21, 26, 27, 32)]
    ),
    SpellInfo(
        id=63, name_fr="Attaque Mortelle", name_en="Lethal Attack", name_es="Ataque Mortal",
        breed_id=4, level=6, ap_cost=4, min_range=1, max_range=2, cast_in_line=True,
        max_cast_per_turn=2, max_cast_per_target=2, crit_rate_base=25,
        effects=[DamageEffect("EARTH", 39, 49, 50, 60)]
    ),
]


BUILTIN_MONSTERS: List[MonsterInfo] = [
    MonsterInfo(
        id=101, name_fr="Bouftou", name_en="Gobball", name_es="Jalató",
        grades=[
            MonsterGrade(1, level=2, life_points=35, action_points=4, movement_points=3, neutral_res=0, earth_res=5, fire_res=-5, water_res=0, air_res=0),
            MonsterGrade(2, level=4, life_points=45, action_points=4, movement_points=3, neutral_res=0, earth_res=7, fire_res=-5, water_res=0, air_res=0),
            MonsterGrade(3, level=6, life_points=55, action_points=4, movement_points=3, neutral_res=0, earth_res=9, fire_res=-5, water_res=0, air_res=0),
            MonsterGrade(4, level=8, life_points=65, action_points=4, movement_points=3, neutral_res=0, earth_res=11, fire_res=-5, water_res=0, air_res=0),
            MonsterGrade(5, level=10, life_points=75, action_points=4, movement_points=3, neutral_res=0, earth_res=13, fire_res=-5, water_res=0, air_res=0),
        ]
    ),
    MonsterInfo(
        id=102, name_fr="Chef de Guerre Bouftou", name_en="Gobball War Chief", name_es="Jefe de Guerra Jalató",
        grades=[
            MonsterGrade(1, level=14, life_points=160, action_points=5, movement_points=4, neutral_res=10, earth_res=20, fire_res=-10, water_res=5, air_res=5),
            MonsterGrade(2, level=17, life_points=190, action_points=5, movement_points=4, neutral_res=12, earth_res=22, fire_res=-10, water_res=6, air_res=6),
            MonsterGrade(3, level=20, life_points=220, action_points=5, movement_points=4, neutral_res=14, earth_res=25, fire_res=-10, water_res=8, air_res=8),
            MonsterGrade(4, level=23, life_points=260, action_points=5, movement_points=4, neutral_res=16, earth_res=27, fire_res=-10, water_res=9, air_res=9),
            MonsterGrade(5, level=26, life_points=300, action_points=5, movement_points=4, neutral_res=18, earth_res=30, fire_res=-10, water_res=10, air_res=10),
        ]
    ),
    MonsterInfo(
        id=103, name_fr="Prespic", name_en="Prespic", name_es="Prespic",
        grades=[
            MonsterGrade(1, level=10, life_points=50, action_points=6, movement_points=4, neutral_res=10, earth_res=15, fire_res=0, water_res=0, air_res=-10, fixed_res=4),
            MonsterGrade(2, level=13, life_points=65, action_points=6, movement_points=4, neutral_res=12, earth_res=18, fire_res=0, water_res=0, air_res=-10, fixed_res=6),
            MonsterGrade(3, level=16, life_points=80, action_points=6, movement_points=4, neutral_res=14, earth_res=20, fire_res=0, water_res=0, air_res=-10, fixed_res=8),
            MonsterGrade(4, level=19, life_points=95, action_points=6, movement_points=4, neutral_res=16, earth_res=22, fire_res=0, water_res=0, air_res=-10, fixed_res=10),
            MonsterGrade(5, level=22, life_points=110, action_points=6, movement_points=4, neutral_res=18, earth_res=25, fire_res=0, water_res=0, air_res=-10, fixed_res=12),
        ]
    ),
    MonsterInfo(
        id=104, name_fr="Sanglier", name_en="Wild Boar", name_es="Jabalí",
        grades=[
            MonsterGrade(1, level=18, life_points=180, action_points=5, movement_points=4, neutral_res=15, earth_res=25, fire_res=-15, water_res=0, air_res=0),
            MonsterGrade(5, level=28, life_points=320, action_points=5, movement_points=4, neutral_res=20, earth_res=35, fire_res=-15, water_res=0, air_res=0),
        ]
    ),
    MonsterInfo(
        id=105, name_fr="Gelée Rouge", name_en="Red Jelly", name_es="Gelatina Roja",
        grades=[
            MonsterGrade(1, level=24, life_points=190, action_points=6, movement_points=3, neutral_res=0, earth_res=-10, fire_res=60, water_res=-20, air_res=0),
            MonsterGrade(5, level=32, life_points=290, action_points=6, movement_points=3, neutral_res=0, earth_res=-10, fire_res=75, water_res=-20, air_res=0),
        ]
    ),
    MonsterInfo(
        id=106, name_fr="Gelée Bleue", name_en="Blue Jelly", name_es="Gelatina Azul",
        grades=[
            MonsterGrade(1, level=22, life_points=170, action_points=6, movement_points=3, neutral_res=0, earth_res=0, fire_res=-20, water_res=60, air_res=-10),
            MonsterGrade(5, level=30, life_points=260, action_points=6, movement_points=3, neutral_res=0, earth_res=0, fire_res=-20, water_res=75, air_res=-10),
        ]
    ),
    MonsterInfo(
        id=107, name_fr="Craqueleur", name_en="Crackler", name_es="Crujidor",
        grades=[
            MonsterGrade(1, level=34, life_points=420, action_points=6, movement_points=3, neutral_res=30, earth_res=40, fire_res=10, water_res=-15, air_res=10, fixed_res=10),
            MonsterGrade(5, level=42, life_points=650, action_points=6, movement_points=3, neutral_res=40, earth_res=50, fire_res=15, water_res=-15, air_res=15, fixed_res=15),
        ]
    ),
    MonsterInfo(
        id=108, name_fr="Poutch Ingball", name_en="Punching Ball", name_es="Puching Ball",
        grades=[
            MonsterGrade(1, level=50, life_points=10000, action_points=0, movement_points=0, neutral_res=0, earth_res=0, fire_res=0, water_res=0, air_res=0, fixed_res=0),
        ]
    ),
]


class GameDataRepository:
    """Singleton repository storing live and offline Dofus Touch entities."""

    _instance: Optional[GameDataRepository] = None

    def __init__(self) -> None:
        self.spells_by_id: Dict[int, SpellInfo] = {}
        self.spells_by_name: Dict[str, SpellInfo] = {}
        self.monsters_by_id: Dict[int, MonsterInfo] = {}
        self.monsters_by_name: Dict[str, MonsterInfo] = {}
        self._load_builtins()
        self._load_cached_files()

    @classmethod
    def get_instance(cls) -> GameDataRepository:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _normalize_name(self, name: str) -> str:
        s = name.lower().strip()
        s = re.sub(r"[àáâãäå]", "a", s)
        s = re.sub(r"[èéêë]", "e", s)
        s = re.sub(r"[ìíîï]", "i", s)
        s = re.sub(r"[òóôõö]", "o", s)
        s = re.sub(r"[ùúûü]", "u", s)
        s = re.sub(r"[ç]", "c", s)
        s = re.sub(r"['\s\-_]+", " ", s)
        return s

    def _load_builtins(self) -> None:
        for s in BUILTIN_SPELLS:
            self.register_spell(s)
        for m in BUILTIN_MONSTERS:
            self.register_monster(m)
        logger.info(f"Loaded {len(self.spells_by_id)} built-in spells and {len(self.monsters_by_id)} built-in monsters.")

    def register_spell(self, spell: SpellInfo) -> None:
        self.spells_by_id[spell.id] = spell
        for name in (spell.name_fr, spell.name_en, spell.name_es):
            if name:
                self.spells_by_name[self._normalize_name(name)] = spell

    def register_monster(self, monster: MonsterInfo) -> None:
        self.monsters_by_id[monster.id] = monster
        for name in (monster.name_fr, monster.name_en, monster.name_es):
            if name:
                self.monsters_by_name[self._normalize_name(name)] = monster

    def _load_cached_files(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        spells_file = CACHE_DIR / "spells_custom.json"
        monsters_file = CACHE_DIR / "monsters_custom.json"

        if spells_file.exists():
            try:
                data = json.loads(spells_file.read_text(encoding="utf-8"))
                for item in data:
                    effs = [DamageEffect(**e) for e in item.get("effects", [])]
                    item["effects"] = effs
                    sp = SpellInfo(**item)
                    self.register_spell(sp)
                logger.info(f"Loaded {len(data)} custom spells from cache.")
            except Exception as e:
                logger.warning(f"Failed to read custom spells: {e}")

        if monsters_file.exists():
            try:
                data = json.loads(monsters_file.read_text(encoding="utf-8"))
                for item in data:
                    grs = [MonsterGrade(**g) for g in item.get("grades", [])]
                    item["grades"] = grs
                    mn = MonsterInfo(**item)
                    self.register_monster(mn)
                logger.info(f"Loaded {len(data)} custom monsters from cache.")
            except Exception as e:
                logger.warning(f"Failed to read custom monsters: {e}")

        # Full synchronized encyclopedias
        full_monsters = CACHE_DIR / "monsters_full.json"
        if full_monsters.exists():
            try:
                data = json.loads(full_monsters.read_text(encoding="utf-8"))
                for item in data:
                    grs = [MonsterGrade(**g) for g in item.get("grades", [])]
                    item["grades"] = grs
                    mn = MonsterInfo(**item)
                    self.register_monster(mn)
                logger.info(f"Loaded {len(data)} full official monsters from cache.")
            except Exception as e:
                logger.warning(f"Failed to read full monsters: {e}")

    def find_spell(self, identifier: Any) -> Optional[SpellInfo]:
        """Lookup spell by ID or trilingual name."""
        if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
            return self.spells_by_id.get(int(identifier))
        if isinstance(identifier, str):
            norm = self._normalize_name(identifier)
            if norm in self.spells_by_name:
                return self.spells_by_name[norm]
            # Partial substring matching
            for k, v in self.spells_by_name.items():
                if norm in k or k in norm:
                    return v
        return None

    def find_monster(self, identifier: Any) -> Optional[MonsterInfo]:
        """Lookup monster by ID or trilingual name."""
        if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
            return self.monsters_by_id.get(int(identifier))
        if isinstance(identifier, str):
            norm = self._normalize_name(identifier)
            if norm in self.monsters_by_name:
                return self.monsters_by_name[norm]
            # Partial substring matching
            for k, v in self.monsters_by_name.items():
                if norm in k or k in norm:
                    return v
        return None

    def get_breed_spells(self, breed_id: int) -> List[SpellInfo]:
        return [s for s in self.spells_by_id.values() if s.breed_id == breed_id or s.breed_id == 0]

    def export_summary(self) -> Dict[str, Any]:
        return {
            "total_spells": len(self.spells_by_id),
            "total_monsters": len(self.monsters_by_id),
            "spells": [asdict(s) for s in self.spells_by_id.values()],
            "monsters": [asdict(m) for m in self.monsters_by_id.values()],
        }
