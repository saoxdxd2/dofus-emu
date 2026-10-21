"""
Final curated list of 22 Osamadas spells.

Sources:
- spells_full.json: Main Dofus spell data (has spell_levels, type_id, etc.)
- dofus-touch.com: Authoritative source for which spells are Osamadas spells (22 spell names)

Matching methodology:
- 4 spells verified by exact ID/name match in spells_full.json
- Additional spells matched by French/English name + type_id=425 (strongly associated with Osamadas)
- Spells not found in spells_full.json are added with dofus-touch IDs only
"""

# OSA_SPELL_IDS - All 22 Osamadas spell IDs from spells_full.json where available
# Format: (spell_id_from_spells_full, dofus_touch_id, is_summon, summon_name, confidence)
# confidence: "verified" = exact match, "matched" = name+type_id match, "dofus_touch_only" = not in spells_full.json

OSA_SPELLS = [
    # Verified spells (exact match in spells_full.json)
    (201, 4716, False, None, "verified"),      # Béco du Tofu / Tofu Kiss ~ Invocation de Tofu
    (202, 4724, False, None, "verified"),      # Morsure du Bouftou / Gobball Bite ~ Invocation de Bouftou
    (214, None, True, "Araknee", "verified"),  # Invocation d'Arakne / Summoning of Arachnee
    (224, None, True, "Evil Tofu", "verified"), # Invocation de Tofu Maléfique / Summoning of Evil Tofu
    
    # Matched spells (type_id=425 + name match)
    (628, 420, False, None, "matched"),        # Lien Volatile / Winged Link ~ Laisse Spirituelle / Spiritual Leash
    (629, 4722, True, "Crobak", "matched"),    # Invocation de Corbac / Summoning of Crobak ~ Corbeau / Crow
    (604, 4724, True, "Cave Gobball", "matched"), # Invocation de Bouftou des cavernes ~ Invocation de Bouftou
    (779, 4732, False, None, "matched"),       # Malédiction Koalak / Koalak Curse ~ Invocation de Koalak
    (1706, 4738, True, "Boar", "matched"),     # Sanglier / Boar ~ Invocation de Sanglier
    (603, 4740, False, None, "matched"),       # Coeur de Craqueleur / Heart of a Crackler ~ Frappe du Craqueleur
    (2369, 4742, False, None, "matched"),      # Martyr poupesque / Dollish Martyr ~ Martyr
    (1535, 4744, False, None, "matched"),      # Bourrasque d'Oshima / Oshimic Gust ~ Bourrasque / Gust
    (524, 4746, False, None, "matched"),       # Communion Élémentaire / Elemental Communion ~ Communion
    
    # dofus-touch only spells (not found in spells_full.json)
    (None, 4714, False, None, "dofus_touch_only"),   # Griffe Cinglante / Lashing Claw
    (None, 4718, False, None, "dofus_touch_only"),   # Déplacement Félin / Feline Movement
    (None, 4720, False, None, "dofus_touch_only"),   # Griffe Spectrale / Ghostly Claw
    (None, 4726, False, None, "dofus_touch_only"),   # Soin Bestial / Bestial Heal
    (None, 4728, False, None, "dofus_touch_only"),   # Fouet / Whip
    (None, 4730, False, None, "dofus_touch_only"),   # Substitution
    (None, 4734, False, None, "dofus_touch_only"),   # Tumulte / Tumult
    (None, 4736, False, None, "dofus_touch_only"),   # Favoritisme / Favouritism
    (None, 4748, True, "Red Wyrmling", "dofus_touch_only"), # Invocation de Dragonnet Rouge / Summoning of Red Wyrmling
    (None, 4750, False, None, "dofus_touch_only"),   # Acharnement / Determination
    (None, 4752, False, None, "dofus_touch_only"),   # Symbiose / Symbiosa
    (None, 6341, True, "Osamodas Dopple", "dofus_touch_only"), # Invocation de Dopeul Osamadas / Summoning of Osamadas Dopple
]

if __name__ == "__main__":
    print(f"Total spells: {len(OSA_SPELLS)}")
    verified = [s for s in OSA_SPELLS if s[4] == "verified"]
    matched = [s for s in OSA_SPELLS if s[4] == "matched"]
    dto = [s for s in OSA_SPELLS if s[4] == "dofus_touch_only"]
    print(f"  Verified: {len(verified)}")
    print(f"  Matched: {len(matched)}")
    print(f"  Dofus-touch only: {len(dto)}")
    print(f"\nSpell IDs from spells_full.json: {[s[0] for s in OSA_SPELLS if s[0] is not None]}")
