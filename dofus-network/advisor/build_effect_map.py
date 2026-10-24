#!/usr/bin/env python3
"""Derive item effect_id -> stat-key mappings from Touch encyclopedia data.

The static item dump (items_full.json) carries raw effect ids but no stat
names; the Touch encyclopedia dump carries stat names and dice ranges but no
effect ids.  Joining both by item id and dice range recovers the mapping
without guessing.

Run from anywhere:
    python -m advisor.build_effect_map
    python dofus-network/advisor/build_effect_map.py

Outputs (advisor/data/):
    effect_map.json             effect_id <-> canonical stat key + confidence
    touch_items.json            per-gid normalized encyclopedia stats/recipes
    touch_sets.json             normalized set bonuses per piece count
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ITEMS_FULL = BASE / "data" / "cache" / "items_full.json"
TOUCH_ITEMS = BASE / "data" / "scratch" / "touch_allequipments.json"
TOUCH_SETS = BASE / "data" / "scratch" / "touch_sets.json"
OUT_DIR = HERE / "data"

ANCHORS = {"111": "action_points", "128": "movement_points"}
MIN_VOTES = 1
MIN_CONFIDENCE = 0.5

# Effect 100 is the weapon hit line: every weapon class (type ids 2-8, 19-22)
# carries it and no other line fits the damage range. The encyclopedia dump has
# no weapons, so this cannot come from the join; the live Effects DB confirms it.
CURATED = {
    "100": {"key": "weapon_damage", "source": "weapon-effect inspection"},
}

# Spell-modification lines (+range/-AP cost/+crit on a named spell). They are
# real effects but not stat points; the character report lists them separately.
SPECIAL_EFFECT_IDS = [str(i) for i in range(281, 291)]


# --------------------------------------------------------------------------
# label normalization
# --------------------------------------------------------------------------

def norm_label(label: str) -> str:
    """Accent/case/space insensitive form. The scrape replaced accents with
    U+FFFD; French stat labels only ever lose an e-family accent there, so the
    replacement is folded to 'e' before stripping diacritics."""
    s = str(label).replace("\ufffd", "e").replace("\u2019", "'")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9%]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


ELEMENTS = {
    "neutre": "neutral",
    "terre": "earth",
    "feu": "fire",
    "eau": "water",
    "air": "air",
}

STAT_KEYS = {
    "pa": "action_points",
    "pm": "movement_points",
    "po": "range",
    "portee": "range",
    "vitalite": "vitality",
    "vie": "life",
    "sagesse": "wisdom",
    "force": "strength",
    "intelligence": "intelligence",
    "chance": "chance",
    "agilite": "agility",
    "puissance": "power",
    "prospection": "prospecting",
    "initiative": "initiative",
    "pods": "pods",
    "dommages": "damage",
    "dommages critiques": "damage_critical",
    "dommages poussee": "damage_push",
    "dommages pieges": "damage_traps",
    "soins": "heals",
    "invocations": "summons",
    "coups critiques": "critical_hit",
    "resistance critiques": "critical_resistance",
    "fuite": "dodge",
    "tacle": "tackle",
    "esquive pa": "dodge_ap",
    "esquive pm": "dodge_mp",
    "retrait pa": "ap_reduction",
    "retrait pm": "mp_reduction",
    "resistance poussee": "res_push",
    "reduction physique": "reduction_physical",
    "reduction magique": "reduction_magic",
    "renvoie dommages": "reflect_damage",
    "puissance pieges": "power_traps",
}

METADATA_LABELS = {
    "exchangeable", "linked", "title", "emote", "quelqu un vous suit",
    "nombre de victimes 0", "",
}

SPECIAL_MARKERS = (
    "sur le sort",
    "sort ",
    "lancer en ligne",
    "ligne de vue",
    "delai de relance",
    "cout en pa",
    "nombre de lancer",
    "portee du sort",
    "change les paroles",
)


def stat_key(label: str) -> tuple[str | None, str]:
    """Return (canonical key, kind). kind: stat | metadata | special | unknown."""
    n = norm_label(label)
    if n in METADATA_LABELS:
        return None, "metadata"
    if n in STAT_KEYS:
        return STAT_KEYS[n], "stat"
    if n == "puissance pieges":
        return "power_traps", "stat"
    if any(m in n for m in SPECIAL_MARKERS):
        return None, "special"

    m = re.match(r"^(% )?resistance (neutre|terre|feu|eau|air)( jcj)?$", n)
    if m:
        key = ("res_percent_" if m.group(1) else "res_fixed_") + ELEMENTS[m.group(2)]
        if m.group(3):
            key = key.replace("res_", "pvp_res_", 1)
        return key, "stat"

    m = re.match(r"^dommages (neutre|terre|feu|eau|air)$", n)
    if m:
        return "damage_" + ELEMENTS[m.group(1)], "stat"

    return None, "unknown"


# --------------------------------------------------------------------------
# loading / dice ranges
# --------------------------------------------------------------------------

def load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def as_int(value, default=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def touch_range(spec) -> tuple[int, int] | None:
    if not isinstance(spec, dict):
        return None
    if "from" not in spec:
        return None
    lo = as_int(spec.get("from"))
    hi = as_int(spec.get("to"), lo)
    return (lo, hi) if "to" in spec else (lo, lo)


def item_range(effect: dict) -> tuple[int, int]:
    lo = as_int(effect.get("dice_num"))
    hi = as_int(effect.get("dice_side"), lo)
    return (lo, hi if hi else lo)


def parse_touch_stats(stats) -> tuple[dict, list, list]:
    """-> ({key: (lo,hi)}, special_labels, unknown_labels)"""
    parsed: dict[str, tuple[int, int]] = {}
    special: list[str] = []
    unknown: list[str] = []
    for line in stats or []:
        if not isinstance(line, dict):
            continue
        for label, spec in line.items():
            key, kind = stat_key(label)
            rng = touch_range(spec)
            if kind == "special":
                if label not in special:
                    special.append(str(label))
                continue
            if kind != "stat" or rng is None:
                if kind == "unknown" and str(label) not in unknown:
                    unknown.append(str(label))
                continue
            prev = parsed.get(key)
            if prev is None:
                parsed[key] = rng
            elif rng[0] < prev[0]:
                parsed[key] = rng
    return parsed, special, unknown


# --------------------------------------------------------------------------
# inference
# --------------------------------------------------------------------------

def infer_effect_map(items_full: dict, touch_items: list) -> dict:
    votes: dict[str, Counter] = defaultdict(Counter)
    samples: dict[tuple[str, int], list[int]] = defaultdict(list)
    matched_items = 0

    for entry in touch_items:
        if not isinstance(entry, dict):
            continue
        gid = as_int(entry.get("_id"), -1)
        item = items_full.get(str(gid)) if isinstance(items_full, dict) else None
        if not item:
            continue
        stats, _, _ = parse_touch_stats(entry.get("stats"))
        if not stats:
            continue

        effects = item.get("effects") or []
        eff_by_range: dict[tuple[int, int], list[int]] = defaultdict(list)
        for eff in effects:
            if not isinstance(eff, dict):
                continue
            eid = as_int(eff.get("effect_id"), -1)
            if eid < 0:
                continue
            eff_by_range[item_range(eff)].append(eid)

        ranges = Counter(stats.values())
        used = False
        for key, rng in stats.items():
            if ranges[rng] != 1:
                continue
            ids = eff_by_range.get(rng) or []
            if len(ids) != 1:
                continue
            eid = ids[0]
            votes[key][eid] += 1
            if len(samples[(key, eid)]) < 5:
                samples[(key, eid)].append(gid)
            used = True
        if used:
            matched_items += 1

    stats_out: dict[str, dict] = {}
    effect_to_key: dict[str, str] = {}
    confidence: dict[str, dict] = {}
    for key, counter in sorted(votes.items()):
        total = sum(counter.values())
        ranked = counter.most_common()
        best_id, best_votes = ranked[0]
        share = best_votes / total if total else 0.0
        stats_out[key] = {
            "effect_ids": [eid for eid, _ in ranked],
            "confidence": round(share, 4),
            "votes": {str(eid): n for eid, n in ranked},
            "samples": samples.get((key, best_id), []),
        }
        if best_votes >= MIN_VOTES and share >= MIN_CONFIDENCE:
            effect_to_key[str(best_id)] = key
            confidence[str(best_id)] = {
                "key": key,
                "votes": best_votes,
                "share": round(share, 4),
            }

    stats_out["_meta"] = {
        "matched_items": matched_items,
        "touch_items": len(touch_items),
        "curated_effects": len(CURATED),
    }

    for eid, spec in CURATED.items():
        key = spec["key"]
        if effect_to_key.get(eid, key) != key:
            raise SystemExit(
                f"curated effect {eid} collides with inferred key "
                f"{effect_to_key[eid]!r} (wanted {key!r})"
            )
        effect_to_key[eid] = key
        confidence[eid] = {"key": key, "votes": 1, "share": 1.0,
                           "source": spec["source"]}
        row = stats_out.setdefault(key, {
            "effect_ids": [], "confidence": 1.0, "votes": {}, "samples": [],
        })
        if int(eid) not in row["effect_ids"]:
            row["effect_ids"].append(int(eid))

    stats_out["_meta"]["unique_effects"] = len(effect_to_key)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "items_full": str(ITEMS_FULL),
            "touch_equipments": str(TOUCH_ITEMS),
        },
        "anchors": ANCHORS,
        "stats": stats_out,
        "effect_to_key": effect_to_key,
        "confidence": confidence,
        "non_stat_effect_ids": SPECIAL_EFFECT_IDS,
    }


# --------------------------------------------------------------------------
# normalization outputs
# --------------------------------------------------------------------------

def normalize_stats_block(stats) -> dict:
    out, _, _ = parse_touch_stats(stats)
    return {
        key: {"min": lo, "max": hi}
        for key, (lo, hi) in sorted(out.items())
    }


def normalize_sets(touch_sets: list) -> dict:
    sets_out = []
    for entry in touch_sets:
        if not isinstance(entry, dict):
            continue
        sid = as_int(entry.get("_id"), -1)
        if sid < 0:
            continue
        bonus_by_count = {}
        for block in entry.get("bonus") or []:
            if not isinstance(block, dict):
                continue
            count = as_int(block.get("number"), 0)
            if count <= 0:
                continue
            bonus_by_count[str(count)] = normalize_stats_block(block.get("stats"))
        sets_out.append({
            "id": sid,
            "name": entry.get("name"),
            "level": as_int(entry.get("lvl"), 0),
            "piece_ids": [as_int(x, -1) for x in entry.get("equipment_id") or []],
            "bonus_by_count": bonus_by_count,
        })
    sets_out.sort(key=lambda s: s["level"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(TOUCH_SETS),
        "sets": sets_out,
    }


def normalize_items(touch_items: list, items_full: dict) -> dict:
    items_out = {}
    for entry in touch_items:
        if not isinstance(entry, dict):
            continue
        gid = as_int(entry.get("_id"), -1)
        if gid < 0:
            continue
        stats, special, unknown = parse_touch_stats(entry.get("stats"))
        full = items_full.get(str(gid)) or {}
        ingredients = []
        for ingredient in entry.get("recipe") or []:
            if not isinstance(ingredient, dict):
                continue
            for _, spec in ingredient.items():
                if not isinstance(spec, dict):
                    continue
                rid = as_int(spec.get("id"), -1)
                qty = as_int(spec.get("quantity"), 0)
                if rid > 0 and qty > 0:
                    ingredients.append([rid, qty])
        items_out[str(gid)] = {
            "id": gid,
            "name": entry.get("name"),
            "type": entry.get("type"),
            "level": as_int(entry.get("lvl"), 0),
            "set_id": as_int(entry.get("setId"), 0),
            "type_id": as_int(full.get("type_id"), 0),
            "stats": {
                key: {"min": lo, "max": hi}
                for key, (lo, hi) in sorted(stats.items())
            },
            "condition": [str(c) for c in entry.get("condition") or []],
            "ingredients": ingredients,
            "special": special,
            "unknown": unknown,
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(TOUCH_ITEMS),
        "items": items_out,
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def run() -> dict:
    items_full_raw = load_json(ITEMS_FULL)
    items_full = {}
    if isinstance(items_full_raw, list):
        for item in items_full_raw:
            if isinstance(item, dict) and item.get("id") is not None:
                items_full[str(item["id"])] = item
    elif isinstance(items_full_raw, dict):
        items_full = items_full_raw

    touch_items = load_json(TOUCH_ITEMS)
    touch_sets = load_json(TOUCH_SETS)

    effect_map = infer_effect_map(items_full, touch_items)
    for eid, key in ANCHORS.items():
        got = effect_map["effect_to_key"].get(eid)
        if got != key:
            raise SystemExit(
                f"anchor broken: effect {eid} inferred as {got!r}, expected {key!r}"
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(OUT_DIR / "effect_map.json", effect_map)
    write_json(OUT_DIR / "touch_items.json", normalize_items(touch_items, items_full))
    write_json(OUT_DIR / "touch_sets.json", normalize_sets(touch_sets))
    return effect_map


def write_json(path: Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    result = run()
    meta = result["stats"]["_meta"]
    print(
        f"mapped {meta['unique_effects']} effect ids from "
        f"{meta['matched_items']}/{meta['touch_items']} encyclopedia items"
    )
    for key in ("action_points", "movement_points", "range", "vitality",
                "strength", "intelligence", "chance", "agility", "power",
                "critical_hit", "damage_critical", "heals"):
        row = result["stats"].get(key)
        if row:
            print(f"  {key:16s} -> {row['effect_ids'][0]} "
                  f"({row['confidence']:.0%}, {sum(row['votes'].values())} votes)")


if __name__ == "__main__":
    main()
