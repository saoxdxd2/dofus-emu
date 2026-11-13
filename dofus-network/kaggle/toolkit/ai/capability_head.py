"""Capability descriptors and the torch-free half of the dynamic tool head.

The shipped tool policy used to end in ``nn.Linear(d_model, 21)``: one output
neuron per tool name. That head has two failures that only show up when the
action space grows:

* a 29th capability has no row, so it can never be emitted;
* adding the row invalidates every checkpoint ever trained, because the shape
  of ``tool_head.weight`` changed.

This module replaces the class list with *data*. Every capability is encoded
into a fixed-width descriptor from its registry entry (id, description, params,
backend, flags). A scorer projects the state embedding and the descriptor into
the same space and takes their dot product. The weights are independent of the
number of tools, so a new capability is one more descriptor row -- no new
parameter, no retraining, no checkpoint migration.

The encoding is deliberately deterministic and dependency-free (``hashlib``
only, never Python's salted ``hash``) so the same capability gets the same row
on every machine, in training and in serving. ``ai/capability_torch.py`` holds
the ``nn.Module``; this file stays importable with numpy alone, which is what
the runtime and the tests use.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

# 64 text-hash dimensions + 16 name-hash + 8 structural + 8 char-hash.
DESCRIPTOR_DIM = 96
_TEXT_DIM = 64
_NAME_DIM = 16
_STRUCT_DIM = 8
_CHAR_DIM = 8

_CAPABILITY_FILES = (
    Path(__file__).resolve().parent.parent / "data" / "capabilities.json",
    Path.cwd() / "dofus-network" / "data" / "capabilities.json",
    Path.cwd() / "data" / "capabilities.json",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _digest(text: str, salt: str) -> int:
    payload = (salt + "\x1f" + text).encode("utf-8", "replace")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "little")


def _signed(text: str, salt: str) -> float:
    return 1.0 if _digest(text, salt) & 1 else -1.0


def _bump(vector: np.ndarray, index: int, value: float) -> None:
    vector[index % len(vector)] += value


def _tokens(record: Dict[str, Any]) -> List[str]:
    """Tokens that describe what the capability *does*.

    The id is split into verb and leaf, camelCase is split, and the parameter
    names/types/enums are folded in. A description-only encoding loses the
    difference between ``hdv.scan`` and ``hdv.list`` when both descriptions
    mention prices; the argument vocabulary is the part that distinguishes
    "read" from "write".
    """
    name = str(record.get("id") or record.get("name") or "")
    name = _CAMEL_RE.sub(" ", name)
    parts = [name, name.replace(".", " ")]
    description = str(record.get("description") or "")
    parts.append(description)
    parts.append(str(record.get("backend") or ""))
    parts.append(str(record.get("directive") or ""))
    parts.append(str(record.get("returns") or ""))
    params = record.get("params") or {}
    if isinstance(params, dict):
        for key, raw in params.items():
            parts.append(str(key))
            if isinstance(raw, dict):
                parts.append(str(raw.get("type") or ""))
                parts.append(str(raw.get("doc") or ""))
                for value in raw.get("enum") or ():
                    parts.append(str(value))
    text = " ".join(parts).lower().replace("_", " ").replace("-", " ")
    return _TOKEN_RE.findall(text)


def encode_descriptor(record: Dict[str, Any]) -> np.ndarray:
    """Encode one capability registry entry into a stable unit vector.

    The output is L2-normalised, so cosine scoring is a dot product and the
    scale does not depend on how verbose a capability's documentation is.
    """
    vector = np.zeros(DESCRIPTOR_DIM, dtype=np.float32)
    tokens = _tokens(record)

    # -- text bag: unigrams, bigrams and character trigrams -----------------
    text = np.zeros(_TEXT_DIM, dtype=np.float32)
    for token in tokens:
        _bump(text, _digest(token, "u"), 1.0)
    for left, right in zip(tokens, tokens[1:]):
        _bump(text, _digest(left + "_" + right, "b"), 0.5)
    joined = " ".join(tokens)
    for start in range(max(0, len(joined) - 2)):
        gram = joined[start:start + 3]
        _bump(text, _digest(gram, "c"), 0.15)
    vector[:_TEXT_DIM] = text

    # -- name hash: a stable identity signal the description cannot wash out -
    name = str(record.get("id") or record.get("name") or "")
    verb = name.split(".", 1)[0]
    name_slot = _digest(name, "name") % _NAME_DIM
    verb_slot = _digest(verb, "verb") % _NAME_DIM
    vector[_TEXT_DIM + name_slot] += 1.5
    vector[_TEXT_DIM + verb_slot] += 1.0

    # -- structural flags ---------------------------------------------------
    params = record.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    required = sum(1 for raw in params.values()
                   if isinstance(raw, dict) and raw.get("required"))
    enums = sum(len(raw.get("enum") or ()) for raw in params.values()
                if isinstance(raw, dict))
    backend = str(record.get("backend") or "local")
    struct = np.array([
        1.0 if record.get("read_only") else 0.0,
        1.0 if record.get("moves") else 0.0,
        1.0 if record.get("lists") else 0.0,
        1.0 if backend == "local" else 0.0,
        1.0 if backend == "directive" else 0.0,
        1.0 if backend == "sentinel" else 0.0,
        min(1.0, len(params) / 8.0),
        min(1.0, (required + enums) / 8.0),
    ], dtype=np.float32)
    vector[_TEXT_DIM + _NAME_DIM:_TEXT_DIM + _NAME_DIM + _STRUCT_DIM] = struct

    # -- character hash: spelling/prefix similarity for near-names ----------
    char = np.zeros(_CHAR_DIM, dtype=np.float32)
    compact = name.lower().replace(".", "")
    for start in range(max(0, len(compact) - 2)):
        _bump(char, _digest(compact[start:start + 3], "ch"), 1.0)
    vector[_TEXT_DIM + _NAME_DIM + _STRUCT_DIM:] = char

    norm = float(np.linalg.norm(vector))
    if norm <= 1e-9:
        vector[0] = 1.0
        norm = 1.0
    return vector / norm


def load_capability_records(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read the capability registry, or return an empty list when absent.

    Training runs on Kaggle with only the corpus mounted; serving has the
    repository. Returning ``[]`` rather than raising keeps the trainer usable
    with a name-only fallback table.
    """
    candidates = [path] if path is not None else list(_CAPABILITY_FILES)
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            if not Path(candidate).exists():
                continue
            payload = json.loads(Path(candidate).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = [row for row in payload.get("capabilities") or []
                if isinstance(row, dict) and row.get("id")]
        if rows:
            return rows
    return []


def _synthetic_record(name: str) -> Dict[str, Any]:
    verb, _, leaf = name.partition(".")
    return {
        "id": name,
        "verb": verb,
        "description": "%s %s capability" % (verb, leaf.replace(".", " ")),
        "backend": "local",
        "params": {},
        "read_only": False,
        "moves": False,
        "lists": False,
    }


def descriptor_table(
    names: Optional[Sequence[str]] = None,
    records: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[List[str], np.ndarray]:
    """Return ``(names, matrix)`` for the requested capability order.

    ``names`` may be a subset (the 21 trained labels) or the full registry.
    Names with no registry entry still get a deterministic descriptor, so a
    checkpoint can be scored against a capability list that is newer than the
    registry it was trained with.
    """
    if records is None:
        records = load_capability_records()
    by_name: Dict[str, Dict[str, Any]] = {
        str(row.get("id")): row for row in records if row.get("id")
    }
    if names is None:
        names = list(by_name)
    ordered = [str(name) for name in names]
    matrix = np.stack([
        encode_descriptor(by_name.get(name) or _synthetic_record(name))
        for name in ordered
    ]).astype(np.float32)
    return ordered, matrix


def merged_tool_order(learned: Sequence[str],
                      records: Optional[Sequence[Dict[str, Any]]] = None
                      ) -> List[str]:
    """Learned labels first, then every other registry capability.

    Keeping the learned names in their original positions means label ids in
    an existing corpus keep their meaning: row ``i`` is still the same tool.
    New capabilities are appended, which is exactly the growth property the
    fixed-width head could not provide.
    """
    if records is None:
        records = load_capability_records()
    ordered = list(dict.fromkeys(str(name) for name in learned))
    seen = set(ordered)
    for row in records:
        name = str(row.get("id") or "")
        if name and name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered


def scores_from_embeddings(states: np.ndarray, descriptors: np.ndarray,
                           scale: float = 10.0) -> np.ndarray:
    """Dot-product scores for serving paths that do not use onnxruntime."""
    states = np.asarray(states, dtype=np.float32)
    descriptors = np.asarray(descriptors, dtype=np.float32)
    states = states / np.maximum(np.linalg.norm(states, axis=-1, keepdims=True), 1e-9)
    descriptors = descriptors / np.maximum(
        np.linalg.norm(descriptors, axis=-1, keepdims=True), 1e-9)
    return (states @ descriptors.T) * float(scale)


def descriptor_manifest(names: Sequence[str],
                        records: Optional[Sequence[Dict[str, Any]]] = None
                        ) -> Dict[str, Any]:
    """Sidecar payload that lets a runtime rebuild the head without torch."""
    ordered, matrix = descriptor_table(names, records)
    return {
        "capability_tools": ordered,
        "descriptor_dim": int(matrix.shape[1]),
        "capability_descriptors": [[round(float(value), 6) for value in row]
                                   for row in matrix],
    }
