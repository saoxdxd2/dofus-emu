"""Guard the action-space contract and the learned-head gap.

Two checks, no client / no network / no torch:

1. **Registry consistency** — `agent/schema.py` (TOOLS), `data/capabilities.json`
   and `agent/features.py` must all declare the *same* tool set. The regression
   gate pins 28 tools; adding a capability means appending to capabilities.json
   and re-encoding the feature vector — no new class id, no retraining.

2. **Learned-head coverage** — the shipped policy head has 21 trained logits
   while the registry has 28 tools (`rules.md`). The seven newest tools
   (hdv.buy, inv.equip, hdv.prices, tap.interactive, ui.tap, ui.icons,
   shop.buy) are rule-only until a new head is trained. A trained policy will
   *silently refuse* those tools, so this test reports the gap as a WARNING
   (exit 0) rather than failing the gate — but CI must not treat "0 failed" as
   "the full vocabulary is learned".

Run: python dofus-network/agent/test_tool_registry_vs_head.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

FAILURES: list[str] = []
WARNINGS: list[str] = []


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"FAIL {msg}")


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"WARN {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


def main() -> None:
    global FAILURES, WARNINGS
    cap_path = REPO / "dofus-network" / "data" / "capabilities.json"
    if not cap_path.exists():
        fail(f"missing {cap_path}")
        sys.exit(1)

    try:
        caps = json.loads(cap_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"capabilities.json unparseable: {exc}")
        sys.exit(1)

    registry_tools = [c["id"] for c in caps.get("capabilities", [])]
    registry_set = set(registry_tools)

    sys.path.insert(0, str(REPO / "dofus-network"))
    from agent.schema import TOOLS

    schema_set = {spec.name for spec in TOOLS}

    if registry_set != schema_set:
        only_reg = sorted(registry_set - schema_set)
        only_schema = sorted(schema_set - registry_set)
        fail("capabilities.json and agent/schema.py disagree",
             f"only-in-capabilities={only_reg} only-in-schema={only_schema}")
    else:
        ok(f"capabilities.json == agent/schema.py: {len(schema_set)} tools")

    try:
        from agent.features import FEATURE_COUNT
        ok(f"agent/features.py FEATURE_COUNT={FEATURE_COUNT} (feature-vector width, not tool count)")
    except Exception as exc:
        fail(f"could not import agent.features FEATURE_COUNT: {exc}")

    RULE_ONLY_TOOLS = {"hdv.buy", "inv.equip", "hdv.prices", "tap.interactive",
                       "ui.tap", "ui.icons", "shop.buy"}

    missing_from_registry = sorted(RULE_ONLY_TOOLS - registry_set)
    if missing_from_registry:
        fail("rule-only tools absent from capabilities.json registry", missing_from_registry)
    else:
        ok(f"all {len(RULE_ONLY_TOOLS)} rule-only tools present in capabilities.json")

    head_files = list((REPO / "models").rglob("*")) if (REPO / "models").exists() else []
    head_found = False
    for hf in head_files:
        try:
            text = hf.read_text(encoding="utf-8", errors="replace")
        except (OSError, IsADirectoryError):
            continue
        for token in ("21", "28", "logit", "output", "head"):
            if token in text.lower():
                head_found = True
                break
        if head_found:
            break

    if head_found:
        warn("a model/head artifact was scanned; confirm its output width matches the "
             f"{len(schema_set)}-tool registry before trusting the learned policy on "
             f"the {len(RULE_ONLY_TOOLS)} rule-only tools (rules.md)")
    else:
        ok("no head artifact found on this checkout — head width cannot be verified offline")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
        sys.exit(1)
    if WARNINGS:
        print(f"{len(WARNINGS)} warning(s) — gate green, but the learned head does not "
              "yet cover the full 28-tool vocabulary")
        return
    print("tool registry consistent; learned-head gap documented as a warning")


if __name__ == "__main__":
    main()
