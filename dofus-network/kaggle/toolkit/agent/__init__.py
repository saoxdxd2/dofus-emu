"""Agent layer for Dofus Touch.

The contract between the models (baseline policy now, the 100M brain later) and
the game:

* ``schema``    — canonical tool definitions with parameter validation.
* ``executor``  — runs a validated tool call against the right backend
                  (local knowledge cache, Sentinel HTTP, or a commander
                  directive for the client-injected JS).
* ``recorder``  — every observation/action/result is appended to JSONL; this
                  log is the raw material for SFT, GRPO and OPD training and
                  for the semantic simulator.
* ``runtime``   — the observe -> decide -> act loop with a pluggable policy
                  (``RulePolicy`` today, neural policy later).
* ``serve``     — stdlib HTTP service (default 127.0.0.1:8895).
* ``episodes``  — scripted collector that turns cache entities into recorded
                  multi-language tool-use episodes.
* ``tool_dataset`` — trajectories -> tool-use SFT JSONL (system prompt + call).
* ``synthetic_tools`` — constructed, schema-valid demonstrations for the tools
                   that need a live client, so training sees the full action
                   space (opt-in source, never executed against the game).
* ``tool_eval`` — golden multilingual goal -> tool-call benchmark.

Stdlib only on purpose: this must run under the bundled ``runtimes/python``
interpreter with no torch, no network access, and while the game is live.
"""

# ruff: noqa: F401 - the TYPE_CHECKING block documents lazy exports

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _lazy_exports import lazy_export

_EXPORTS = {
    "TOOL_SCHEMA": "agent.schema",
    "ToolSpec": "agent.schema",
    "tool_names": "agent.schema",
    "validate_call": "agent.schema",
    "TrajectoryRecorder": "agent.recorder",
    "ToolExecutor": "agent.executor",
    "ToolResult": "agent.executor",
    "AgentRuntime": "agent.runtime",
    "RulePolicy": "agent.runtime",
    "ToolCall": "agent.runtime",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Import public names on first use.

    ``agent.jsonio`` and ``agent.recorder`` are used by the combat server and
    lightweight tools; importing the whole executor/runtime/urllib stack just
    because the package was touched adds startup time and resident memory to
    processes that never execute a world action.
    """
    return lazy_export(globals(), _EXPORTS, __name__, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


if TYPE_CHECKING:  # pragma: no cover - type checkers only
    from agent.executor import ToolExecutor, ToolResult
    from agent.recorder import TrajectoryRecorder
    from agent.runtime import AgentRuntime, RulePolicy, ToolCall
    from agent.schema import TOOL_SCHEMA, ToolSpec, tool_names, validate_call

