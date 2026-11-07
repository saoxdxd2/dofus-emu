"""PEP 562 lazy package exports shared by the package __init__ modules."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Dict, MutableMapping


def lazy_export(namespace: MutableMapping[str, Any], exports: Dict[str, str],
                package: str, name: str) -> Any:
    module_name = exports.get(name)
    if module_name is None:
        raise AttributeError("module %r has no attribute %r" % (package, name))
    value = getattr(import_module(module_name), name)
    namespace[name] = value
    return value
