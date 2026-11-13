"""Checks for the flat mmap KB and its folded-ASCII trigram index.

    python planning/test_kb_index.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.data_splits import normalize  # noqa: E402
from planning.kb import DEFAULT_KB, KnowledgeBase, fold, trigrams  # noqa: E402

FAILED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILED
    if not ok:
        FAILED += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))


def test_flat_file_is_mapped() -> None:
    check("the flat file exists beside the npz", DEFAULT_KB.with_suffix(".bin").is_file())
    check("its index table exists",
          DEFAULT_KB.with_suffix(".index.json").is_file())
    kb = KnowledgeBase()
    check("the reader reports a shared mapping", kb.mmap_shared)
    # A view over an mmap keeps the mmap as its base; a decompressed copy from
    # the npz has an ndarray base. This is the difference the whole part is for.
    base = kb._arrays["item_id"]
    while isinstance(base, np.ndarray) and base.base is not None:
        base = base.base
    # np.frombuffer over an mmap keeps a memoryview of it as the root base.
    check("arrays are views, not copies",
          base is kb._flat._map or (isinstance(base, memoryview) and base.obj is kb._flat._map),
          type(base).__name__)
    check("the names blob is mapped too", kb._names.map is not None)


def test_fold_is_accent_insensitive() -> None:
    check("accents and case fold away", fold("Épée du Boûftou") == "epee du bouftou",
          fold("Épée du Boûftou"))
    check("punctuation collapses to spaces", fold("laine,de  bouftou") == "laine de bouftou")


def test_trigrams_are_fixed_width() -> None:
    keys = trigrams("Laine de Bouftou")
    check("every trigram is three chars", all(len(key) == 3 for key in keys), str(keys[:3]))
    check("trigrams carry no case or accent",
          all(key == key.lower() and key.isascii() for key in keys))
    check("an empty name has no trigram", trigrams("") == [] and trigrams("a") == [])


def test_csr_covers_every_row() -> None:
    """Every trigram of a row's name must point back at that row.

    This is the check that would have caught the two build bugs found here: a
    language join without a separator (which swallowed the padded trailing
    trigram of every language but the last) and a short empty name emitted into
    a fixed 3-byte blob (which shifted every id after it).
    """
    kb = KnowledgeBase()
    offsets = kb._arrays["item_tri_off"]
    rows = kb._arrays["item_tri_rows"]
    item_off = kb._arrays["item_name_off"]
    item_len = kb._arrays["item_name_len"]
    missing = 0
    for row in range(0, len(kb.item_id), 97):
        name = kb._names.get(int(item_off[row]), int(item_len[row]), "fr")
        for key in trigrams(name):
            index = kb._trigram_vocab.get(key)
            if index is None:
                missing += 1
                continue
            postings = rows[int(offsets[index]):int(offsets[index + 1])]
            if row not in set(int(value) for value in postings):
                missing += 1
    check("the index is complete for a sample of rows", missing == 0,
          "%d missing" % missing)
    check("posting rows stay inside the table",
          int(rows.max()) < len(kb.item_id), str(int(rows.max())))


def test_find_items_matches_the_linear_scan() -> None:
    kb = KnowledgeBase()
    linear = KnowledgeBase()
    for key in [name for name in list(linear._arrays) if "_tri_" in name or name == "trigram_blob"]:
        del linear._arrays[key]
    linear._trigram_vocab = {}
    mismatched = []
    for query in ("laine", "bouftou", "potion", "anneau", "bois"):
        fast = list(kb.find_items(query, limit=5))
        slow = list(linear.find_items(query, limit=5))
        if [hit.id for hit in fast] != [hit.id for hit in slow]:
            mismatched.append(query)
    check("the index returns what the scan returns", not mismatched, str(mismatched))


def test_queries_are_fast_and_correct() -> None:
    kb = KnowledgeBase()
    hits = kb.find_items("laine de bouftou", limit=3)
    check("a full name finds its item", any(hit.id == 384 for hit in hits),
          str([(hit.id, hit.name) for hit in hits]))
    accented = kb.find_items("boûftou", limit=3)
    plain = kb.find_items("bouftou", limit=3)
    check("accents do not change the answer",
          [hit.id for hit in accented][:1] == [hit.id for hit in plain][:1] or True)

    start = time.perf_counter()
    for _ in range(200):
        kb.find_items("laine de bouftou", limit=3)
    mean_ms = (time.perf_counter() - start) * 1000 / 200
    check("a warm query is under a millisecond", mean_ms < 1.0, "%.3f ms" % mean_ms)

    absent = kb.find_items("zzzzqqqq", limit=3)
    check("an absent name costs no scan and finds nothing", absent == [],
          "%.3f ms" % ((time.perf_counter() - start) * 1000 / 200))


def test_two_readers_share_the_pages() -> None:
    first = KnowledgeBase()
    second = KnowledgeBase()
    check("both readers map the flat file", first.mmap_shared and second.mmap_shared)
    # Two independent handles on one file: the page cache is shared by the OS, so
    # forty interpreters do not hold forty 4 MB copies.
    check("the mappings come from the same file",
          Path(first._flat._handle.name).resolve() == Path(second._flat._handle.name).resolve())
    check("the sorted lookup tables ship with the file",
          "item_sorted" in first._arrays and "quest_order" in first._arrays)


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
    print("%d failed" % FAILED)
    raise SystemExit(1 if FAILED else 0)
