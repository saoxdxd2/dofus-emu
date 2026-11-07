"""Fast JSON when orjson is available, stdlib JSON everywhere else.

orjson is roughly four times faster on the large observation documents this
project exchanges over HTTP; the fallback keeps the embedded runtime and the
Kaggle toolkit dependency-free.
"""

from __future__ import annotations

import json
from typing import Any

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover - exercised where orjson is absent
    _orjson = None

_OPTIONS = getattr(_orjson, "OPT_NON_STR_KEYS", 0) if _orjson is not None else 0


def dumps_bytes(value: Any) -> bytes:
    if _orjson is not None:
        try:
            return _orjson.dumps(value, option=_OPTIONS)
        except TypeError:
            # Types or keys the installed orjson cannot represent still have a
            # stdlib path; the fallback keeps a logging call from killing a run.
            pass
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def dumps(value: Any) -> str:
    return dumps_bytes(value).decode("utf-8")


def loads(data: Any) -> Any:
    if _orjson is not None:
        return _orjson.loads(data)
    if isinstance(data, (bytes, bytearray, memoryview)):
        data = bytes(data).decode("utf-8")
    return json.loads(data)


def iter_rows(path: Any):
    """Every JSON object in a line-delimited log, one record per object.

    Two reasons a line is not one record here. A writer that forgets its trailing
    newline glues two objects onto one line, and reading it with ``json.loads(line)``
    throws *both* away -- 108 such lines exist across the logs this project reads
    (G97), which is how §12.2 under-counted 68 step rows and 27 intervention rows.
    And a process killed mid-write leaves a torn tail. Decoding object by object keeps
    every whole record and drops only the fragment that cannot be read.

    Non-dict lines are not yielded: every caller of this indexes the row.
    """
    from pathlib import Path

    path = Path(path)
    if not path.is_file():
        return
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text.startswith("{"):
                continue
            index = 0
            while index < len(text):
                try:
                    row, index = decoder.raw_decode(text, index)
                except ValueError:
                    break           # a torn tail: the rest of this line is unreadable
                if isinstance(row, dict):
                    yield row
                while index < len(text) and text[index] in " \t":
                    index += 1


def iter_array(path: Any, chunk_bytes: int = 1 << 20):
    """Every object of a JSON array file, one at a time, holding one block of the file at a time.

    `data/cache/items_full.json` is a single-line array of 21,554 item records. `loads()` on it
    builds every dict before the index projects each one down to six fields: measured 2026-11-03 on
    the shipped interpreter, 63.9 MB of the 121.7 MB the item kind costs a running brain, and CPython
    hands those pages back only when the whole graph dies -- so an installed process pays for the
    array for its entire life. Decoding record by record removes the dict tree; reading the file in
    blocks (`chunk_bytes`, default 1 MiB) removes the 24 MB string too, which is what the *peak*
    was: a brain daemon that indexes seven kinds peaked at 138 MB, and twelve of them is a real
    number on a 7.8 GB host.

    A file whose top level is not an array yields nothing, matching the caller that has always
    ignored non-list payloads rather than half-parsing them. A record that will not decode stops the
    iteration at that point, once the file is exhausted -- a torn tail costs the rows after it, not
    the rows before it.
    """
    import codecs
    from pathlib import Path

    path = Path(path)
    if not path.is_file():
        return
    decoder = json.JSONDecoder()
    text_decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buf = ""
    pos = 0
    eof = False
    started = False

    with path.open("rb") as handle:
        def fill() -> bool:
            """Append the next block of characters; False once the file is exhausted."""
            nonlocal buf, eof
            if eof:
                return False
            raw = handle.read(chunk_bytes)
            if not raw:
                eof = True
                buf += text_decoder.decode(b"", True)
                return False
            buf += text_decoder.decode(raw)
            return True

        while True:
            while True:
                if pos >= len(buf):
                    if not fill():
                        return
                    continue
                character = buf[pos]
                if character in " \t\r\n":
                    pos += 1
                    continue
                if not started:
                    # The first thing that is not whitespace decides the shape of the file.
                    if character != "[":
                        return
                    started = True
                    pos += 1
                    continue
                if character == ",":
                    pos += 1
                    continue
                if character == "]":
                    return
                break
            if pos >= len(buf) or buf[pos] != "{":
                return                          # junk, or a non-object element
            try:
                row, pos = decoder.raw_decode(buf, pos)
            except ValueError:
                if not fill():
                    return                      # torn tail or corrupt record: stop, keep what we got
                continue                        # the object simply is not all here yet
            yield row
            if len(buf) - pos < 4096:
                buf = buf[pos:]                 # drop what has been consumed, keep one window
                pos = 0
            elif pos > 4 * chunk_bytes:
                buf = buf[pos:]
                pos = 0


def utf8_console() -> None:
    """Force stdout/stderr to UTF-8 so printed data survives a Windows console.

    A tool that prints captured game text (`ensure_ascii=False`) writes it in the
    console's own encoding, and on Windows that is cp1252. Then an accented quest line
    comes out as bytes no reader can decode as UTF-8, and a character cp1252 does not
    have at all - an arrow, a CJK name - raises UnicodeEncodeError and kills the report
    mid-print. Measured 2026-10-31 on `tools/session_report.py --json` over the real
    captures, which is the instrument the live session reads its numbers out of.
    """
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass    # a stream that cannot be reconfigured is not worth a crash
