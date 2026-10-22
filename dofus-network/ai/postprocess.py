"""Deterministic post-processing for encoder calls — one job: fix what the NN can't.

Where the 30M model actually fails
----------------------------------
Measured on the frame-held-out split, the remaining call errors are almost never
*tool* errors. They are argument errors, in three families:

1. **Truncated spans.** The span decoder emits a fixed budget of characters, so
   ``Éklat de Kroktail la Désaltérante`` comes back as ``Éklat de Kroktail la
   Désalt`` and ``IVOIRE Marqueur Aerafal`` as ``VOIRE Marqueur``. Those
   characters are *gone from the model output* — no trimmer can recover them.
   Only a lookup against the goal text can.
2. **Clause swallowing.** ``Whispering Dagger using median_minus_1`` — the span
   ran past the entity into the price rule. A trimmer *can* fix this.
3. **Invented numbers.** The model copies an unrelated digit into ``quantity``,
   ``min_stock`` or ``price`` (``vends 6 Parchemins`` -> ``min_stock: 6``).

None of that is a language problem. It is entity resolution and number parsing —
exactly the work a small model should not be asked to do from 256 tokens.

What this module does
---------------------
1. **coerce** a string to its declared type;
2. **ground entity spans** in the goal via the surface gazetteer, whether the
   field is missing or merely truncated — this is what repairs family 1;
3. **extract free text** that is genuinely present (``reason`` after the verb,
   ``chat.say`` text in quotes or after a colon);
4. **read a coordinate pair** the goal states;
5. **fill a lone number into a *required* int field** — never into an optional
   one, because an unstated optional value is a schema default, not a mystery;
6. **restore digits the decoder dropped** — a value that is a strict prefix of a
   number the goal states is the same truncation defect as family 1, and gets
   the same repair;
7. **resolve opaque identifiers** — gazetteer first, KB only on a unique exact
   match.

What it deliberately does not do
--------------------------------
It never overwrites a value the model produced with a *different* fact, never
adds an optional field the model did not emit, and never guesses. An earlier
version filled ``duration_min`` from any lone number in the goal, which turned
correct ``farm.start`` calls into invalid ones and made the whole layer net
negative on accuracy. The three rules in ``clean_call``'s docstring are the
contract; the per-tool regression check in ``ai/bench_encoder`` is what enforces
them.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ai.data_splits import normalize

# ---------------------------------------------------------------------------
# number words (1-100, four languages)
# ---------------------------------------------------------------------------

_UNITS: Dict[str, Dict[str, int]] = {
    "en": {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
           "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
           "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19},
    "fr": {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
           "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11,
           "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15,
           "seize": 16, "dix-sept": 17, "dix-huit": 18, "dix-neuf": 19},
    "es": {"uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
           "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
           "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
           "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19},
    "de": {"eins": 1, "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6,
           "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11,
           "zwolf": 12, "dreizehn": 13, "vierzehn": 14, "funfzehn": 15,
           "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19},
}
_TENS: Dict[str, Dict[str, int]] = {
    "en": {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
           "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90},
    "fr": {"vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50,
           "soixante": 60, "soixante-dix": 70, "quatre-vingts": 80,
           "quatre-vingt-dix": 90},
    "es": {"veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50,
           "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90},
    "de": {"zwanzig": 20, "dreissig": 30, "vierzig": 40, "funfzig": 50,
           "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90},
}


def parse_number(text: str) -> Optional[int]:
    """Parse a number written in any of the four languages, or digits."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if digits:
        return int(digits)
    words = re.findall(r"[a-zà-ÿ-]+", normalize(text))
    if not words:
        return None
    total = 0
    current = 0
    matched = False
    for word in words:
        hit = None
        for lang in _UNITS:
            if word in _UNITS[lang]:
                hit = _UNITS[lang][word]
                break
        if hit is not None:
            current += hit
            matched = True
            continue
        for lang in _TENS:
            if word in _TENS[lang]:
                current += _TENS[lang][word]
                matched = True
                break
    total += current
    return total if matched else None


# ---------------------------------------------------------------------------
# free-text extraction
# ---------------------------------------------------------------------------

# A quote is a *paired* delimiter. A bare apostrophe is not one: French and
# Spanish elision ("l'équipe", "d'urgence", "s'il") puts an apostrophe between
# two letters, and the previous single-character class read it as an opening
# quote -- so "préviens l'équipe : j'arrive" extracted "équipe : j" and
# "arrêt d'urgence s'il te plaît" extracted "urgence s". Both are garbage, and
# the second one invented a ``safe.stop`` reason out of a bare trigger.
_DQUOTE = re.compile("[\"“]([^\"”]{1,200})[\"”]")
_GUILLEMET = re.compile("«([^»]{1,200})»")
_SQUOTE = re.compile("(?<![A-Za-zÀ-ÿ])[']([^']{1,200})['](?![A-Za-zÀ-ÿ])")
_AFTER_COLON = re.compile(r"[:：]\s*(.+)$", re.DOTALL)
_QUOTE_CHARS = " \t\r\n.,;:!?\"'«»“”„‘’"


def extract_text(goal: str) -> Optional[str]:
    """The clause the goal isolates: quoted text first, then after a colon.

    Returns ``None`` when the goal isolates nothing, which is the answer for a
    bare trigger like ``"arrête tout"``. Callers must not substitute the whole
    goal for a missing clause -- that is how a post-processor invents values.
    """
    goal = goal or ""
    for pattern in (_GUILLEMET, _DQUOTE, _SQUOTE):
        match = pattern.search(goal)
        if match and match.group(1).strip(_QUOTE_CHARS):
            return match.group(1).strip(_QUOTE_CHARS)
    match = _AFTER_COLON.search(goal)
    if match and match.group(1).strip(_QUOTE_CHARS):
        return match.group(1).strip(_QUOTE_CHARS)
    return None


# Verbs that precede the payload in a goal, per language. Used to trim spans.
_LEAD_VERBS = re.compile(
    r"^(?:"
    r"(?:vends?|vendre|vend(e|re)|ache(?:te|ter)|va(?:s)?|aller|suis|arre(?:te|ter)"
    r"|liste(?:r)?|montre[rz]?|donne[rz]?|dis|fais|cherche[rz]?|trouve[rz]?"
    r"|avance[rz]?|continue[rz]?|rejoins|passe[rz]?|scanne[rz]?|verifie[rz]?"
    r"|mets|propose[rz]?|place[rz]?|envoie[rz]?|farm(?:e|ea|ear)?|commence"
    r"|sell|buy|go|follow|stop|list|show|give|say|tell|find|search|look"
    r"|advance|continue|join|pass|scan|check|put|offer|message|inform|start"
    r"|vende[rz]?|compra[rz]?|ve|sigue|para|deten|busca|encuentra|dame|muestra"
    r"|di|haz|unete|pasa|escanea|consulta|pon|ofrece|avanza|progresa|empieza"
    r"|verkauf[e]?|kauf[e]?|geh|folge|stoppe|liste|zeige|gib|sag|such|finde"
    r"|mach|tritt|gib|uberspringe|prufe|biete|setze|bewege|erreiche|beginne"
    r")\s+)+",
    re.IGNORECASE,
)
# Trailing clause markers: everything after these is not part of an entity name.
_TAIL_MARKERS = re.compile(
    r"\s+(?:sur le march[eé]|en sous-cotant|pour \d+|à \d+ kamas?|for \d+ kamas?"
    r"|at \d+ kamas?|auf dem markt|fur \d+ kamas?|por \d+ kamas?|en el mercado"
    r"|de mon inventaire|from my inventory|aus meinem inventar|de mi inventario"
    r"|limite \d+|limit \d+|niveau \d+|level \d+|in \w+|à \w+|en \w+|in \w+)\s*.*$",
    re.IGNORECASE,
)
# "au prix médian moins 1" / "at median minus 1" / "a precio mediano menos 1":
# the price-rule name contains a digit, and it is not a quantity. Strip it before
# looking for numbers or it both blocks the fill (two numbers) and fabricates one.
_PRICE_RULE = re.compile(
    r"(?:prix|precio|preis|price)?\s*(?:m[eé]dian|median|mediano)\s*"
    r"(?:moins|minus|menos|-)\s*(?:1|one|un|une|ein|uno)\b",
    re.IGNORECASE,
)


def strip_price_rule(text: str) -> str:
    return _PRICE_RULE.sub(" ", text or "")


_ARTICLES = re.compile(
    r"^(?:le |la |les |l['’]|un |une |des |du |de |d['’]|the |a |an |el |los |las |"
    r"del |de |der |die |das |den |dem |ein |eine )", re.IGNORECASE)

# The two quest indexes are keyed differently, and the difference matters. The
# gazetteer's is keyed on the goal tail *with* the quest noun ("work on quest
# 18"), because that is how the corpus writes it. The KB's is keyed on the
# quest's own name, so the noun has to come off before the lookup.
_QUEST_NOUN = re.compile(
    r"^(?:qu[eê]tes?|quests?|misi[oó]n(?:es)?|mission(?:en)?|aufgabe(?:n)?)\s+",
    re.IGNORECASE,
)


def quest_tail(goal: str) -> str:
    """The quest *name* a goal states: verbs, articles and the quest noun gone.

    Kept for the gazetteer side, which is keyed on the goal tail. It is *not*
    used to resolve a quest id, because the quest noun can sit anywhere in a
    goal ("mach mit Quest Ghulsaison weiter", "fais l'etape suivante de la
    quete Offrande") and a name can contain commas, so no amount of lead-verb
    stripping recovers the span reliably. ``resolve_quest_id`` scans the other
    way and needs no span at all.
    """
    value = clean_span(_LEAD_VERBS.sub("", goal or "").strip(" .,!?:;"))
    for _ in range(3):
        trimmed = _QUEST_NOUN.sub("", value)
        if trimmed == value:
            break
        value = clean_span(trimmed)
    return value


def resolve_quest_id(goal: str, kb: Any, lang: Optional[str] = None) -> Optional[int]:
    """The quest a goal names, or ``None`` -- shared by serving and the corpus
    builder so the two cannot drift apart.

    The goal's own language is tried first. A miss there is not a miss: the
    language detector abstains on short goals (``detect`` answers ``auto``), and
    a goal may name a quest by a name the KB holds only in another language.
    Scanning every language is safe, because a false hit needs a *unique*
    cross-language quest name to occur inside a goal that names no quest --
    measured at 0 in 465 id-bearing goals on this corpus.

    ``None`` means either "names no quest" or "names several equally well", and
    both must cost the caller the fill. The KB holds 34 distinct quest rows
    that share the name "Merry Kwismas" in all five languages; a player could
    not resolve that either, and neither may the resolver.
    """
    if kb is None:
        return None
    quest_id = kb.resolve_quest_reference(goal, lang)
    if quest_id is not None or not lang:
        return quest_id
    return kb.resolve_quest_reference(goal, None)


def clean_span(text: str) -> str:
    """Strip leading verbs/articles and trailing clauses from a model span."""
    value = (text or "").strip().strip(" .,!?:;\"'")
    if not value:
        return value
    value = _LEAD_VERBS.sub("", value)
    value = _TAIL_MARKERS.sub("", value)
    for _ in range(3):
        trimmed = _ARTICLES.sub("", value)
        if trimmed == value:
            break
        value = trimmed
    return value.strip(" .,!?:;\"'")


# ---------------------------------------------------------------------------
# per-tool rules
# ---------------------------------------------------------------------------

# Entity fields: the value is a *name* that a gazetteer can ground in the goal.
# These are the fields where a 30M span decoder truncates -- measured on v8, the
# raw output for hdv/inv item spans included 'Éklat de Kroktail la Désalt' for
# 'Éklat de Kroktail la Désaltérante', 'VOIRE Marqueur...' for 'IVOIRE Marqueur...'
# and 'Parchemins' for 'Parchemin de Vaelor'. Those characters are simply gone
# from the model output; no trimmer can recover them. Only a lookup against the
# goal text can.
_ENTITY_FIELDS = ("item", "monster", "resource", "query", "zone", "target",
                  "zaap", "npc", "name", "area")
# Integer fields that may legitimately be filled from a lone number in the goal.
# Only *required* ones: an optional field left empty means the player did not say
# it, and the schema default applies. Filling an optional int from an unrelated
# number is what put a bogus ``duration_min`` into every ``farm.start`` call.
_COORD_FIELDS = ("x", "y")


def _coords_from_goal(goal: str) -> Optional[Tuple[int, int]]:
    """``[5, -9]`` / ``x=5, y=-9`` / ``5,-9`` -> (5, -9)."""
    text = goal or ""
    bracket = re.search(r"\[\s*(-?\d+)\s*[,;]\s*(-?\d+)\s*\]", text)
    if bracket:
        return int(bracket.group(1)), int(bracket.group(2))
    named = re.search(r"\bx\s*[=:]\s*(-?\d+)\D+y\s*[=:]\s*(-?\d+)", text, re.IGNORECASE)
    if named:
        return int(named.group(1)), int(named.group(2))
    return None


def _repair_truncated_numbers(
    cleaned: Dict[str, Any], params: Dict[str, Any], goal: str
) -> None:
    """Restore digits the decoder dropped, in place.

    The fixed character budget truncates *numbers* exactly as it truncates
    spans: measured on v8's predictions against the 3,056-row val, 21 of the
    344 id-bearing ``quest.advance`` rows come back as a strict prefix of the id
    the goal states -- ``807 -> 80``, ``5340 -> 53``, ``3183 -> 318``. The full
    number is in the goal, so this is the same repair as the truncated-span rule
    above: same fact, complete.

    Two guards, because a prefix is also a perfectly good value:

    * **a value sitting at its schema default is never rewritten.** The model was
      trained to materialise defaults for fields the player did not state, the
      canonical comparator forgives exactly that, and the number is therefore the
      canonical "absent" -- not a truncation. Rewriting ``min_stock: 1`` into
      ``1698`` because the goal mentions an unrelated quest id invents a number
      and cost 115 val rows the first time this rule was measured without the
      guard.
    * **the model's value must appear nowhere in the goal.** "niveau 5 à 55"
      states 5; a model that answered 5 is right and must not be "repaired" to
      55. This is the collision the span rule refuses for the same reason.

    And exactly one number in the goal may extend the value: two candidates is a
    guess, and rule 3 forbids guessing.
    """
    stated = [int(n) for n in re.findall(r"\d+", strip_price_rule(goal or ""))]
    if not stated:
        return
    for key, value in list(cleaned.items()):
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        param = params.get(key)
        if param is None or param.get("type") != "int":
            continue
        if value == param.get("default"):
            continue  # the schema default means "not stated"; never rewrite it
        if value in stated:
            continue  # the goal says this number; the model is grounded
        digits = str(value)
        longer = [n for n in stated
                  if str(n).startswith(digits) and len(str(n)) > len(digits)]
        if len(longer) == 1:
            cleaned[key] = longer[0]


def clean_call(
    tool: str,
    args: Dict[str, Any],
    goal: str,
    gazetteer: Any = None,
    kb: Any = None,
    lang: str = "fr",
) -> Dict[str, Any]:
    """Return a cleaned copy of ``args``.

    Three rules, and nothing else:

    1. **Fill only what is justified.** A missing argument is filled only when the
       value is grounded -- a gazetteer surface form found in the goal, a number
       the goal actually contains, or a coordinate pair it actually states. A
       missing *optional* argument is left missing; ``validate_call`` applies the
       schema default.
    2. **Never overwrite a model value, never add a field the model did not
       emit** -- except to repair a truncated span with the gazetteer's canonical
       form for a surface that *is* in the goal, to repair a truncated number
       with the digits the goal states, and to coerce a string to its declared
       type. Both repairs replace a value with the same fact in the right shape.
    3. **Refuse to guess.** If nothing grounds the value, the argument is left
       exactly as the model produced it. A wrong call the agent can detect beats
       a plausible one it cannot.

    It never changes the tool, and never invents a value it cannot ground.
    """
    from agent.schema import TOOLS_BY_NAME

    spec = TOOLS_BY_NAME.get(tool)
    if spec is None:
        return dict(args or {})
    params = spec.params
    cleaned = dict(args or {})
    goal = goal or ""

    def declared(field: str) -> bool:
        return field in params

    def required(field: str) -> bool:
        return bool(params[field].get("required"))

    # -- 1. coerce: a string where the schema says int is the same value in the
    #       wrong shape, so replacing it is not overwriting.
    for key, value in list(cleaned.items()):
        if declared(key) and params[key].get("type") == "int" and isinstance(value, str):
            parsed = parse_number(value)
            if parsed is not None:
                cleaned[key] = parsed

    # -- 2. ground entity spans against the goal. Runs whether the field is
    #       missing or merely truncated, because truncation is the dominant
    #       failure and only a lookup can undo it.
    #
    #       The replacement is restricted to *truncation*: the model's span must
    #       be a strict substring of the grounded form. Anything else is left
    #       alone, because the corpus contains prefix-colliding synthetic names
    #       ("SkiNi Shard" and "SkiNi Shard 89" are two different items), so a
    #       gazetteer hit that is *shorter* than the model's span is a different
    #       entity, not a correction. Overwriting there turned correct calls
    #       into wrong ones -- 34 of them across hdv.list/inv.sell/nav.goto/
    #       hdv.scan/fight.join, all invisible to the containment-based release
    #       comparator and all real under exact matching.
    if gazetteer is not None:
        for field in _ENTITY_FIELDS:
            if not declared(field):
                continue
            found = None
            for source in (field, "item" if field == "resource" else field):
                found = gazetteer.lookup_name(source, goal)
                if found:
                    break
            if not found:
                continue
            current = cleaned.get(field)
            if current is None:
                if required(field):
                    cleaned[field] = found
            elif isinstance(current, str):
                cur, fnd = normalize(current), normalize(found)
                if cur == fnd:
                    continue  # already the canonical spelling
                if len(fnd) > len(cur) and cur in fnd:
                    # Truncation: the model's span is a prefix of a known
                    # surface form. Restore the characters the span decoder
                    # dropped -- same fact, complete.
                    cleaned[field] = found
                # Otherwise the model's span is either more specific than
                # anything we can ground, or unrelated. Rule 3: refuse to guess.

    # -- 3. free text that is genuinely in the goal. Prefer the clause the goal
    #       isolates (quoted, or after a colon): "parada de emergencia: low hp"
    #       states the reason *after* the colon, so filling ``reason`` with the
    #       whole goal string is both sloppy and -- because the release
    #       comparator accepts containment for text arguments -- silently
    #       unrewarding.
    #
    #       When the goal isolates no clause, ``reason`` is filled only from a
    #       *known* reason phrase, which is what the gazetteer's phrase table
    #       holds. The earlier whole-goal fallback invented a reason for every
    #       bare trigger -- "Notstopp bitte" became reason="Notstopp bitte" --
    #       and turned 16 correct ``safe.stop`` calls into wrong ones while
    #       *raising* the release-gate score, because containment forgave it.
    #       A bare trigger gets no reason: the player did not state one.
    if declared("reason") and not cleaned.get("reason"):
        text = extract_text(goal)
        if text is None and gazetteer is not None:
            text = gazetteer.lookup_name("reason", goal)
        if text:
            cleaned["reason"] = text[:200]
    # ``chat.say`` text has no such table: its vocabulary is two-letter greetings
    # ("gg", "ok") that no bounded matcher can ground, and the model never omits
    # it. Only the clause the goal isolates counts.
    if declared("text") and not cleaned.get("text"):
        text = extract_text(goal)
        if text:
            cleaned["text"] = text[:200]

    # -- 4. a coordinate pair the goal states.
    if declared("x") and declared("y"):
        pair = _coords_from_goal(goal)
        if pair is not None:
            for field, value in zip(_COORD_FIELDS, pair):
                if not cleaned.get(field):
                    cleaned[field] = value

    # -- 5. a lone number, for a *required* int field only. "quêtes niveau 55 à
    #       80" carries two numbers and is left alone; so is any optional field,
    #       because an unstated quantity is a schema default, not a mystery.
    numbers = re.findall(r"\d+", strip_price_rule(goal))
    if len(numbers) == 1:
        value = int(numbers[0])
        for field, param in params.items():
            if param.get("type") != "int" or not param.get("required"):
                continue
            if not cleaned.get(field):
                cleaned[field] = value
                break

    # -- 5b. digits the decoder dropped. Runs *after* the lone-number fill so a
    #       field that fill just grounded is not re-examined, and only rewrites a
    #       value that is a strict prefix of a number the goal states.
    _repair_truncated_numbers(cleaned, params, goal)

    # -- 6. resolve opaque identifiers. Gazetteer first (train-grounded), KB only
    #       as a unique match -- taking the first of several ranked hits is a
    #       guess, and rule 3 forbids it.
    for field in ("quest_id", "map_id"):
        if not declared(field) or cleaned.get(field) is not None:
            continue
        resolved = None
        if gazetteer is not None:
            if field == "quest_id":
                resolved = gazetteer.lookup_quest_id(goal)
            if resolved is None:
                resolved = gazetteer.lookup_id(field, goal)
        if resolved is None and kb is not None and field == "quest_id":
            resolved = resolve_quest_id(goal, kb, lang)
        if resolved is not None:
            try:
                cleaned[field] = int(resolved)
            except (TypeError, ValueError):
                cleaned[field] = resolved

    # -- 7. drop anything the capability does not declare: post-processing may not
    #        add arguments the tool never had.
    cleaned = {key: value for key, value in cleaned.items() if key in params}

    # -- 8. an unexecutable price rule. `price_rule=fixed` without an integer price
    #        fails `validate_call`, and the failure costs the whole call: a correct
    #        tool choice and a correct item span thrown away over one optional
    #        field. Measured on v11's val split, 459 of 800 rows in one block were
    #        exactly this -- the goal says "put X on the market", states no price,
    #        and the model still emits `fixed`.
    #
    #        Dropping the field is not guessing a price; it is declining to state
    #        one, which is what the gold label does too (the schema default then
    #        applies at execution). A goal that *did* state a price keeps the rule,
    #        because the model then emits `price` as an int and this branch is not
    #        taken.
    if cleaned.get("price_rule") == "fixed" and not isinstance(cleaned.get("price"), int):
        cleaned.pop("price_rule", None)
    return cleaned
