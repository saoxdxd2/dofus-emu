"""Backwards-compatible alias for the RIPE Atlas collector."""

from __future__ import annotations

from .atlas import *  # noqa: F401,F403
from .atlas import CollectorConfig, collect_ripe_atlas  # noqa: F401
