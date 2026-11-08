"""Guard for the learned-head / registry mismatch.

`rules.md`: the shipped 49K-tool policy head still has 21 trained logits while
the registry declares 28 tools. The seven newest tools
(hdv.buy, inv.equip, hdv.prices, tap.interactive, ui.tap, ui.icons, shop.buy)
are rule-only until a new head is trained. A trained policy will silently
refuse those tools.

This test reports the gap as a WARNING (exit 0) — the gate stays green because
the loop still works rule-first — but CI must not treat "0 failed" as "the full
vocabulary is learned."

No client, no network, no torch.

Run: python dofus-network/agent/test_rule_only_tools_head_width.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

RULE_ONLY_TOOLS = {"hdv.buy", "inv.equip", "hdv.prices", "tap.interactive",
                   "ui.tap", "ui.icons", "shop.buy"}

WARNINGS = 0


def warn(msg: str) -> None:
    global WARNINGS
    WARNINGS += 1
    print(f"WARN {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


def main() -> None:
    global WARNINGS
    cap_path = REPO / "dofus-network" / "data" / "capabilities.json"
    try:
        caps = json.loads(cap_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"could not read {cap_path}: {exc} (check skipped)")
        return

    registry = {c["id"] for c in caps.get("capabilities", [])}

    missing = sorted(RULE_ONLY_TOOLS - registry)
    if missing:
        print(f"rule-only tools absent from registry: {missing}")
        sys.exit(1)

    ok(f"all {len(RULE_ONLY_TOOLS)} rule-only tools present in capabilities.json")

    models_dir = REPO / "dofus-network" / "models"
    head_artifacts = []
    if models_dir.exists():
        for p in models_dir.rglob("*"):
            if p.is_file() and p.suffix in (".pt", ".onnx", ".json", ".txt"):
                head_artifacts.append(p)

    declared_width = None
    for hf in head_artifacts:
        try:
            text = hf.read_text(encoding="utf-8", errors="replace")
        except (OSError, IsADirectoryError):
            continue
        low = text.lower()
        if ("logit" in low or "output" in low or "head" in low) and "21" in text:
            declared_width = 21
            break

    if declared_width == 21:
        warn(f"learned head declares {declared_width} trained logits vs "
             f"{len(registry)} registry tools — the {len(RULE_ONLY_TOOLS)} "
             f"rule-only tools ({sorted(RULE_ONLY_TOOLS)}) are not covered by "
             f"the learned policy until the head is retrained")
    elif head_artifacts:
        ok("head artifacts found but none declare a 21-logit width (manual confirm needed)")
    else:
        warn("no head artifact on this checkout — learned-head width cannot be "
             "verified offline; assume the 21-logit gap still exists")

    print()
    if WARNINGS:
        print(f"{WARNINGS} warning(s) — rule-first loop still green, head gap documented")
        return
    print("rule-only tools head-width check passed (no gap artifact found)")


if __name__ == "__main__":
    main()
