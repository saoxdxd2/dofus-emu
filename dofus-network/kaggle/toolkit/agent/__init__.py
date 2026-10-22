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

from agent.schema import TOOL_SCHEMA, ToolSpec, tool_names, validate_call
from agent.recorder import TrajectoryRecorder
from agent.executor import ToolExecutor, ToolResult
from agent.runtime import AgentRuntime, RulePolicy, ToolCall

__all__ = [
    "TOOL_SCHEMA",
    "ToolSpec",
    "tool_names",
    "validate_call",
    "TrajectoryRecorder",
    "ToolExecutor",
    "ToolResult",
    "AgentRuntime",
    "RulePolicy",
    "ToolCall",
]
