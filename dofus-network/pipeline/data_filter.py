"""Data filter for Dofus game extracts -> THX-01 decision-training format.

Reads raw .py and .json extracts from data/raw/, cleans messy game text,
normalises/translates into model-ready language, and emits THX-01 compatible
JSONL training records: {state, questions, answers, metadata}.

THX-01 question types we emit for Dofus:
  - choice: combat action selection, NPC dialogue option, item use
  - score: confidence in action validity (0-1 ordinal)
  - number: item ID, map coordinate, quantity, level
  - excerpt: verbatim item name, spell name, NPC quote from state
  - cite: supporting span offsets for the answer
"""

from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path
from typing import Any

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")

# ---------------------------------------------------------------------------
# Messy game text normalisation
# ---------------------------------------------------------------------------

# Common Dofus artefacts found in raw extracts
_ARTIFACT_PATTERNS = [
    (r"\[DOFUS\s*[^\]]+\]", ""),            # [DOFUS ...] tags
    (r"<[^>]+>", ""),                        # HTML/XML remnants
    (r"\x1b\[[0-9;]*m", ""),                 # ANSI colour codes
    (r"\s{2,}", " "),                        # collapse whitespace
]

# Language normalisation map (messy -> clean) for French/English Dofus terms
_NORMALISATION = {
    "pv": "points de vie",
    "pa": "points d action",
    "pm": "points de mouvement",
    "po": "portee",
    "inv": "inventaire",
    "pnj": "non joueur",
    "sort": "spell",
    "objet": "item",
    "carte": "map",
    "combat": "fight",
    "lvl": "level",
    "xp": "experience",
    "kam": "kamas",
    "dofus": "dofus",
}


def normalize_text(text: str) -> str:
    """Strip game artefacts and normalise messy Dofus shorthand."""
    if not text:
        return ""
    for pat, repl in _ARTIFACT_PATTERNS:
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)
    lowered = text.lower()
    for messy, clean in _NORMALISATION.items():
        lowered = re.sub(rf"\b{re.escape(messy)}\b", clean, lowered)
    return text.strip()


def extract_items_from_json(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a Dofus JSON extract into a list of item records."""
    records: list[dict[str, Any]] = []

    def walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")
        elif isinstance(obj, str) and obj.strip():
            records.append({"path": path, "text": normalize_text(obj), "raw": obj})

    walk(raw)
    return records


def extract_comments_from_py(raw_py: str) -> list[dict[str, Any]]:
    """Pull comment strings and docstrings from a Python game file."""
    records: list[dict[str, Any]] = []
    # crude but effective: grab # comments and triple-quoted strings
    for m in re.finditer(r"#\s*(.+)", raw_py):
        records.append({"path": "comment", "text": normalize_text(m.group(1)), "raw": m.group(1)})
    for m in re.finditer(r'"""([^"]+)"""', raw_py, flags=re.DOTALL):
        records.append({"path": "docstring", "text": normalize_text(m.group(1)), "raw": m.group(1)})
    return records


def make_training_record(
    state: str,
    question_type: str,
    question: str,
    answer: Any,
    criteria: dict[str, Any] | None = None,
    citation: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single THX-01 training JSONL record."""
    record: dict[str, Any] = {
        "id": hashlib.sha256((state + question + str(answer)).encode()).hexdigest()[:16],
        "state": state[:1024],  # THX-01 truncates at 1024 tokens
        "questions": {
            question_type: {
                "type": question_type,
                "question": question,
                "criteria": criteria or {},
            }
        },
        "answers": {question_type: answer},
        "metadata": metadata or {},
    }
    if citation:
        record["answers"][question_type] = {
            "value": answer,
            "cite": citation,
        }
    return record


def filter_directory(raw_dir: Path | str = RAW_DIR, out: Path | str = PROCESSED_DIR) -> list[Path]:
    """Process all raw extracts and write JSONL training files."""
    raw_dir = Path(raw_dir)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    for py_file in sorted(raw_dir.glob("*.py")):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        records = extract_comments_from_py(text)
        _write_jsonl(out / f"{py_file.stem}_py.jsonl", records)
        written.append(out / f"{py_file.stem}_py.jsonl")

    for json_file in sorted(raw_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        records = extract_items_from_json(data)
        _write_jsonl(out / f"{json_file.stem}_json.jsonl", records)
        written.append(out / f"{json_file.stem}_json.jsonl")

    return written


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    files = filter_directory()
    print(f"Wrote {len(files)} processed files to {PROCESSED_DIR}")