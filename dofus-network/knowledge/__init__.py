"""Canonical, read-only knowledge for the strategist.

The store is built offline from the cached French data and, later, from the
Touch client DB. Runtime code opens it read-only; nothing learns from it.
"""

from .store import CanonicalStore, LANGUAGES, build_store

__all__ = ["CanonicalStore", "LANGUAGES", "build_store"]
