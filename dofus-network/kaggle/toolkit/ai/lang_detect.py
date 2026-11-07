"""Deterministic language detection for EN / FR / ES / DE — no model, no AI.

Why it is needed before training
--------------------------------
The corpus rows carry ``lang: "auto"`` (all 28,476 of them), so:

* per-language accuracy cannot be measured — a German regression hides inside an
  average;
* ``nav.goto`` replies in French regardless of the player's language, because
  ``validate_call`` fills ``lang="fr"`` from the schema default and nothing ever
  overrides it.

Both are fixed by labelling the text. A 4-language detector over short imperative
game orders does not need a neural net: function words and diacritics are enough,
and the result is auditable.

Used for:
    * labelling ``core_sft_v4`` rows (per-language metrics);
    * inferring the reply language from the player's message;
    * flagging rows the detector is unsure about, so they are not counted as
      evidence for any language.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

LANGS = ("en", "fr", "es", "de")

# Function words and imperatives that survive the short, telegraphic style of
# game orders. Overlapping words ("de", "la", "a") are deliberately omitted: they
# cost more confusion than they buy.
MARKERS: Dict[str, Tuple[str, ...]] = {
    # Words shared across languages ("a", "in", "on", "is", "farm", "quest") are
    # deliberately excluded: they cancel out at best and mislead at worst.
    "en": (
        "the", "of", "to", "for", "with", "my", "your", "i", "you", "we",
        "they", "want", "sell", "buy", "go", "follow", "stop", "where", "how",
        "much", "price", "search", "find", "give", "show", "say", "tell",
        "please", "thanks", "hello", "now", "all", "everything", "join",
        "pass", "turn", "market", "inventory", "look", "what", "advance",
        "them", "him", "her", "this", "that", "at", "from", "list", "dungeon",
        # shared with German on purpose: they cancel instead of misleading
        "quest", "quests", "level", "in",
    ),
    "fr": (
        "le", "les", "des", "du", "une", "et", "pour", "avec", "dans", "sur",
        "je", "tu", "il", "nous", "vous", "mon", "ma", "mes", "ce", "cette",
        "vends", "vendre", "achète", "va", "aller", "suis", "suivre", "arrête",
        "où", "combien", "prix", "niveau", "cherche", "trouve", "donne",
        "montre", "dis", "fais", "peux", "veux", "oui", "non", "merci",
        "bonjour", "salut", "stp", "svp", "maintenant", "tout", "tous", "quête",
        "quêtes", "rejoins", "passe", "tour", "marché", "inventaire", "liste",
        "quel", "quelle", "est", "au", "aux", "en", "un", "s'il", "plaît",
        "boucle", "aussi", "puis", "peux", "dois", "veux", "monde", "carte",
    ),
    "es": (
        "el", "los", "las", "del", "una", "y", "para", "con", "por", "que",
        "qué", "mi", "mis", "tu", "tus", "vende", "vender", "compra", "ve",
        "sigue", "para", "detén", "dónde", "cuánto", "precio", "nivel", "busca",
        "encuentra", "dame", "muestra", "di", "haz", "puedes", "quieres", "sí",
        "gracias", "hola", "ahora", "todo", "todos", "misión", "misiones",
        "únete", "pasa", "turno", "mercado", "inventario", "lista", "cuál",
        "es", "en", "un", "por", "favor", "sígueme", "sigueme", "farmea",
        "farmear", "ve", "dame", "espera", "ataca", "camina", "llévame",
    ),
    "de": (
        "der", "die", "das", "den", "dem", "des", "eine", "und", "für", "mit",
        "auf", "zu", "von", "mein", "meine", "ich", "du", "er", "sie", "wir",
        "ihr", "verkaufe", "kaufen", "kaufe", "geh", "folge", "stoppe", "wo",
        "wie", "viel", "preis", "level", "suche", "finde", "gib", "zeige",
        "sag", "mach", "kannst", "willst", "ja", "nein", "danke", "hallo",
        "jetzt", "alles", "quest", "quests", "beitreten", "zug", "markt",
        "inventar", "liste", "welche", "was", "ist", "bitte", "avanciere",
        "farme", "mir", "mich", "weiter", "lass", "aus", "noch", "schon",
        "nächste", "nächsten", "greif", "warte", "bewege", "laufe",
        # shared with English on purpose: they cancel instead of misleading
        "quest", "quests", "level", "in",
    ),
}

# Diacritics that only one of the four languages uses (in this vocabulary).
DIACRITICS: Dict[str, str] = {
    "fr": "èêàçùôîûëïœ",
    "es": "ñ¿¡íóú",
    "de": "ßäöü",
    "en": "",
}

_WORD = re.compile(r"[a-zà-ÿß]+", re.IGNORECASE)


@dataclass
class Detection:
    lang: str
    confidence: float          # margin between the best and the runner-up
    scores: Dict[str, float]
    decided: bool              # False when the margin is too small to trust

    def as_dict(self) -> Dict[str, object]:
        return {
            "lang": self.lang,
            "confidence": round(self.confidence, 3),
            "decided": self.decided,
            "scores": {k: round(v, 2) for k, v in sorted(self.scores.items())},
        }


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text.lower())


def detect(text: str, prior: Optional[str] = None, min_gap: float = 1.0) -> Detection:
    """Score each language by marker hits and diacritics.

    A language is only returned when it leads the runner-up by at least one
    marker (``min_gap``), because a wrong label is worse than no label: an
    abstention keeps the row out of per-language metrics instead of polluting
    them. ``prior`` (e.g. the session locale) breaks ties instead of guessing.
    """
    folded = _fold(text or "")
    words: List[str] = _WORD.findall(folded)
    scores: Dict[str, float] = {lang: 0.0 for lang in LANGS}

    for word in words:
        for lang, markers in MARKERS.items():
            if word in markers:
                scores[lang] += 1.0

    for lang, chars in DIACRITICS.items():
        if not chars:
            continue
        hits = sum(1 for ch in folded if ch in chars)
        if hits:
            scores[lang] += 0.6 * hits

    if prior in scores:
        scores[prior] += 0.5   # a nudge, not a decision

    ordered = sorted(scores.items(), key=lambda pair: -pair[1])
    best_lang, best = ordered[0]
    runner_up = ordered[1][1]
    total = sum(scores.values())

    if total <= 0:
        # Nothing recognisable: fall back to the prior if we have one.
        lang = prior if prior in LANGS else "auto"
        return Detection(lang, 0.0, scores, decided=bool(prior in LANGS))

    gap = best - runner_up
    margin = gap / max(total, 1e-6)
    decided = gap >= min_gap and best >= 1.0
    return Detection(best_lang if decided else (prior or "auto"), margin, scores, decided)


def label_rows(rows: Iterable[Dict[str, object]], key: str = "goal") -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    """Attach a ``lang`` field to flat corpus rows and report the distribution."""
    from collections import Counter

    out: List[Dict[str, object]] = []
    counts: Counter = Counter()
    undecided = 0
    for row in rows:
        detection = detect(str(row.get(key) or ""))
        labelled = dict(row)
        labelled["lang"] = detection.lang
        if not detection.decided:
            undecided += 1
        counts[detection.lang] += 1
        out.append(labelled)
    return out, {"distribution": dict(sorted(counts.items())), "undecided": undecided}


def reply_language(goal: str, default: str = "fr") -> str:
    """Language the agent should answer in, for ``nav.goto.lang`` and friends."""
    detection = detect(goal)
    return detection.lang if detection.lang in LANGS else default


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import json
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", nargs="*", help="detect these strings")
    parser.add_argument("--score-gold", action="store_true",
                        help="measure accuracy against agent.tool_eval.GOLD labels")
    parser.add_argument("--label-split", type=Path, default=None,
                        help="rewrite a jsonl split with lang labels")
    args = parser.parse_args(argv)

    if args.text:
        for text in args.text:
            print(f"{text!r:50s} -> {json.dumps(detect(text).as_dict())}")
        return 0

    if args.score_gold:
        from agent.tool_eval import GOLD

        correct = 0
        total = 0
        by_lang: Dict[str, List[int]] = {}
        for case in GOLD:
            detection = detect(case["goal"])
            hit = detection.lang == case["lang"]
            correct += int(hit)
            total += 1
            bucket = by_lang.setdefault(case["lang"], [0, 0])
            bucket[0] += int(hit)
            bucket[1] += 1
        print(f"GOLD language detection: {correct}/{total} = {correct / total:.1%}")
        for lang, (ok, count) in sorted(by_lang.items()):
            print(f"  [{lang}] {ok}/{count} = {ok / count:.0%}")
        return 0

    if args.label_split:
        from ai.data_splits import load_jsonl, write_jsonl

        rows = load_jsonl(args.label_split)
        labelled, report = label_rows(rows)
        write_jsonl(args.label_split, labelled)
        print(json.dumps(report, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
