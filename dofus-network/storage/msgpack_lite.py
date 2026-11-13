"""Dependency-free MessagePack subset, enough for trajectory records.

Why not the ``msgpack`` wheel
-----------------------------
The shipped interpreter carries no ``msgpack`` (checked: ``import msgpack`` fails on
the embedded 3.11 runtime), and a storage layer that cannot write its own shards on
an installed copy is not a storage layer. This module implements exactly the types a
trajectory row uses -- nil, bool, int, float, str, bytes, list, dict -- and refuses
the rest loudly instead of guessing.

The decoder is offset-based on purpose: a shard keeps one offset per record, so a
slice query decodes only the requested records out of a decompressed block instead
of materialising the whole block first.
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Tuple

__all__ = ["packb", "pack", "unpackb", "unpack_from", "MsgpackError"]

_NIL = 0xC0
_FALSE = 0xC2
_TRUE = 0xC3
_BIN8, _BIN16, _BIN32 = 0xC4, 0xC5, 0xC6
_FLOAT64 = 0xCB
_UINT8, _UINT16, _UINT32, _UINT64 = 0xCC, 0xCD, 0xCE, 0xCF
_INT8, _INT16, _INT32, _INT64 = 0xD0, 0xD1, 0xD2, 0xD3
_FIXSTR = 0xA0
_STR8, _STR16, _STR32 = 0xD9, 0xDA, 0xDB
_FIXARRAY = 0x90
_ARRAY16, _ARRAY32 = 0xDC, 0xDD
_FIXMAP = 0x80
_MAP16, _MAP32 = 0xDE, 0xDF


class MsgpackError(ValueError):
    """A value or buffer this subset cannot represent."""


# -- writing ----------------------------------------------------------------

def pack(value: Any) -> bytes:
    out = bytearray()
    _write(out, value)
    return bytes(out)


def packb(value: Any) -> bytes:  # alias, mirrors the msgpack API
    return pack(value)


def _write(out: bytearray, value: Any) -> None:
    if value is None:
        out.append(_NIL)
        return
    if value is True:
        out.append(_TRUE)
        return
    if value is False:
        out.append(_FALSE)
        return
    if isinstance(value, int) and not isinstance(value, bool):
        _write_int(out, value)
        return
    if isinstance(value, float):
        out.append(_FLOAT64)
        out.extend(struct.pack(">d", value))
        return
    if isinstance(value, str):
        _write_str(out, value)
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        size = len(data)
        if size <= 0xFF:
            out.extend((_BIN8, size))
        elif size <= 0xFFFF:
            out.append(_BIN16)
            out.extend(struct.pack(">H", size))
        else:
            out.append(_BIN32)
            out.extend(struct.pack(">I", size))
        out.extend(data)
        return
    if isinstance(value, (list, tuple)):
        size = len(value)
        if size <= 0x0F:
            out.append(_FIXARRAY | size)
        elif size <= 0xFFFF:
            out.append(_ARRAY16)
            out.extend(struct.pack(">H", size))
        else:
            out.append(_ARRAY32)
            out.extend(struct.pack(">I", size))
        for item in value:
            _write(out, item)
        return
    if isinstance(value, dict):
        size = len(value)
        if size <= 0x0F:
            out.append(_FIXMAP | size)
        elif size <= 0xFFFF:
            out.append(_MAP16)
            out.extend(struct.pack(">H", size))
        else:
            out.append(_MAP32)
            out.extend(struct.pack(">I", size))
        for key, item in value.items():
            if not isinstance(key, str):
                raise MsgpackError("map keys must be strings, got %r" % (type(key).__name__,))
            _write_str(out, key)
            _write(out, item)
        return
    raise MsgpackError("cannot pack %r" % (type(value).__name__,))


def _write_int(out: bytearray, value: int) -> None:
    if 0 <= value <= 0x7F:
        out.append(value)
    elif -32 <= value < 0:
        out.append(value & 0xFF)
    elif 0 <= value <= 0xFF:
        out.extend((_UINT8, value))
    elif 0 <= value <= 0xFFFF:
        out.append(_UINT16)
        out.extend(struct.pack(">H", value))
    elif 0 <= value <= 0xFFFFFFFF:
        out.append(_UINT32)
        out.extend(struct.pack(">I", value))
    elif 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        out.append(_UINT64)
        out.extend(struct.pack(">Q", value))
    elif -0x80 <= value < 0:
        out.append(_INT8)
        out.extend(struct.pack(">b", value))
    elif -0x8000 <= value < 0:
        out.append(_INT16)
        out.extend(struct.pack(">h", value))
    elif -0x80000000 <= value < 0:
        out.append(_INT32)
        out.extend(struct.pack(">i", value))
    else:
        out.append(_INT64)
        out.extend(struct.pack(">q", value))


def _write_str(out: bytearray, value: str) -> None:
    data = value.encode("utf-8")
    size = len(data)
    if size <= 0x1F:
        out.append(_FIXSTR | size)
    elif size <= 0xFF:
        out.extend((_STR8, size))
    elif size <= 0xFFFF:
        out.append(_STR16)
        out.extend(struct.pack(">H", size))
    else:
        out.append(_STR32)
        out.extend(struct.pack(">I", size))
    out.extend(data)


# -- reading ----------------------------------------------------------------

def unpackb(data: Any) -> Any:
    value, offset = unpack_from(data, 0)
    if offset != len(data):
        raise MsgpackError("trailing bytes: %d of %d consumed" % (offset, len(data)))
    return value


def unpack_from(data: Any, offset: int = 0) -> Tuple[Any, int]:
    """Decode one value starting at ``offset``; returns (value, next_offset)."""
    if isinstance(data, memoryview):
        view = data
    else:
        view = memoryview(data)
    return _read(view, offset)


def _read(view: memoryview, offset: int) -> Tuple[Any, int]:
    if offset >= len(view):
        raise MsgpackError("unexpected end of buffer at offset %d" % offset)
    code = view[offset]
    offset += 1

    if code <= 0x7F:                       # positive fixint
        return code, offset
    if code >= 0xE0:                       # negative fixint
        return code - 0x100, offset
    if _FIXSTR <= code <= (_FIXSTR | 0x1F):
        return _read_str(view, offset, code & 0x1F)
    if _FIXARRAY <= code <= (_FIXARRAY | 0x0F):
        return _read_array(view, offset, code & 0x0F)
    if _FIXMAP <= code <= (_FIXMAP | 0x0F):
        return _read_map(view, offset, code & 0x0F)

    if code == _NIL:
        return None, offset
    if code == _FALSE:
        return False, offset
    if code == _TRUE:
        return True, offset
    if code == _FLOAT64:
        return struct.unpack_from(">d", view, offset)[0], offset + 8
    if code == _UINT8:
        return view[offset], offset + 1
    if code == _UINT16:
        return struct.unpack_from(">H", view, offset)[0], offset + 2
    if code == _UINT32:
        return struct.unpack_from(">I", view, offset)[0], offset + 4
    if code == _UINT64:
        return struct.unpack_from(">Q", view, offset)[0], offset + 8
    if code == _INT8:
        return struct.unpack_from(">b", view, offset)[0], offset + 1
    if code == _INT16:
        return struct.unpack_from(">h", view, offset)[0], offset + 2
    if code == _INT32:
        return struct.unpack_from(">i", view, offset)[0], offset + 4
    if code == _INT64:
        return struct.unpack_from(">q", view, offset)[0], offset + 8
    if code in (_BIN8, _BIN16, _BIN32, _STR8, _STR16, _STR32,
                _ARRAY16, _ARRAY32, _MAP16, _MAP32):
        return _read_extended(view, offset, code)
    raise MsgpackError("unsupported msgpack code 0x%02x at offset %d" % (code, offset - 1))


def _read_extended(view: memoryview, offset: int, code: int) -> Tuple[Any, int]:
    if code == _BIN8:
        size = view[offset]
        offset += 1
        return bytes(view[offset:offset + size]), offset + size
    if code == _BIN16:
        size = struct.unpack_from(">H", view, offset)[0]
        offset += 2
        return bytes(view[offset:offset + size]), offset + size
    if code == _BIN32:
        size = struct.unpack_from(">I", view, offset)[0]
        offset += 4
        return bytes(view[offset:offset + size]), offset + size
    if code == _STR8:
        size = view[offset]
        return _read_str(view, offset + 1, size)
    if code == _STR16:
        size = struct.unpack_from(">H", view, offset)[0]
        return _read_str(view, offset + 2, size)
    if code == _STR32:
        size = struct.unpack_from(">I", view, offset)[0]
        return _read_str(view, offset + 4, size)
    if code == _ARRAY16:
        size = struct.unpack_from(">H", view, offset)[0]
        return _read_array(view, offset + 2, size)
    if code == _ARRAY32:
        size = struct.unpack_from(">I", view, offset)[0]
        return _read_array(view, offset + 4, size)
    if code == _MAP16:
        size = struct.unpack_from(">H", view, offset)[0]
        return _read_map(view, offset + 2, size)
    size = struct.unpack_from(">I", view, offset)[0]
    return _read_map(view, offset + 4, size)


def _read_str(view: memoryview, offset: int, size: int) -> Tuple[str, int]:
    raw = view[offset:offset + size]
    if len(raw) != size:
        raise MsgpackError("truncated string: wanted %d, have %d" % (size, len(raw)))
    return bytes(raw).decode("utf-8", "replace"), offset + size


def _read_array(view: memoryview, offset: int, size: int) -> Tuple[List[Any], int]:
    out: List[Any] = []
    for _ in range(size):
        item, offset = _read(view, offset)
        out.append(item)
    return out, offset


def _read_map(view: memoryview, offset: int, size: int) -> Tuple[Dict[str, Any], int]:
    out: Dict[str, Any] = {}
    for _ in range(size):
        key, offset = _read(view, offset)
        if not isinstance(key, str):
            raise MsgpackError("map key is not a string: %r" % (key,))
        value, offset = _read(view, offset)
        out[key] = value
    return out, offset
