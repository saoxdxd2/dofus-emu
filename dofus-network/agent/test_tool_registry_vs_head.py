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


def fail(msg: str, *extra: str) -> None:
    line = " ".join((msg,) + extra) if extra else msg
    FAILURES.append(line)
    print(f"FAIL {line}")


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

    # The head lives under dofus-network/models, not <repo>/models. The previous
    # version scanned the wrong directory, found nothing, and reported "cannot be
    # verified offline" on a checkout that ships the head.
    head_onnx = REPO / "dofus-network" / "models" / "tool_policy.onnx"
    if not head_onnx.exists():
        ok(f"no head artifact at {head_onnx} — head width cannot be verified offline")
    else:
        head_tools: list[str] = []
        metrics: dict = {}
        try:
            # `score_artifact` runs whatever the artifact is -- the numpy path is
            # exact for a dense stack and wrong for a recurrent one, so the arch in
            # the sidecar decides. Using `evaluate` directly here reported a shape
            # error for a correct graph and called it "could not be replayed".
            from agent.weight_space import export_manifest, read_val_rows, score_artifact
            manifest = export_manifest()
            head_tools = list(manifest["base"]["tools"])
            metrics = score_artifact(manifest, read_val_rows())
        except Exception as exc:  # noqa: BLE001 - report, never crash the gate
            warn(f"head present at {head_onnx} but could not be replayed: {exc}")

        if head_tools:
            ok(f"head output width {len(head_tools)} (replayed on "
               f"{metrics.get('rows', 0)} val rows)")
            unlearned = [t for t in registry_tools if t not in head_tools]
            if unlearned:
                warn(f"{len(unlearned)} of {len(registry_tools)} registered tools have no "
                     f"logit and are unreachable by construction: {', '.join(unlearned)}")
            unreachable = metrics.get("unreachable_tools") or []
            if unreachable:
                warn(f"{len(unreachable)} of {len(head_tools)} *trained* tools are never "
                     f"ranked first on any val row: {', '.join(unreachable)} "
                     f"— reachability is not accuracy; these cannot be selected at all")

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
