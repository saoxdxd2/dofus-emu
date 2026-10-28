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
