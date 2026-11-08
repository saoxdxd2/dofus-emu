"""Experiment: does episodic 'never repeat a failed action' cut the loop?

Replays the val episodes step by step. Baseline = the teacher's choice (what the
policy imitates). Masked = the same, except a tool already seen to fail for the
same objective earlier in the episode is skipped and the best *untried* offered
tool is taken instead (highest gain among the row's candidate set).

This is a conservative offline proxy for the live memory: it masks at the
(tool) level per (episode, objective), while the live memory keys on
(tool, args), so the live agent keeps more freedom than this simulation.

Reports, per step: repeat-failure rate and realized gain (baseline / masked /
oracle), plus how many substitutions fired and their net gain.

    python tools/exp_episodic_mask.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

BRAIN = Path(__file__).resolve().parent.parent / "dofus-network"


def load(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def order_key(r):
    return (r.get("step") if r.get("step") is not None else 0,
            r.get("t") if r.get("t") is not None else 0)


def main() -> int:
    rows = load(BRAIN / "data" / "tool_sft_v3" / "val.jsonl")
    episodes = defaultdict(list)
    for r in rows:
        episodes[r.get("ep")].append(r)

    total = 0
    base_repeats = 0
    mask_repeats = 0
    substituted = 0
    chosen_unknown_gain = 0

    base_gain = []       # realized gain of the choice, where its gain is known
    mask_gain = []
    oracle_gain = []
    sub_delta = []       # gain(masked choice) - gain(teacher) on substituted steps

    for _ep, seq in episodes.items():
        failed_keys = set()          # (objective_type, tool) that failed this episode
        tried_keys = set()           # (objective_type, tool) already attempted
        for r in sorted(seq, key=order_key):
            teacher = r.get("teacher") or r.get("tool")
            if not teacher:
                continue
            otype = r.get("objective_type")
            gains = r.get("gains") or {}
            total += 1
            teacher_key = (otype, teacher)

            # baseline: does the teacher repeat a known failure?
            if teacher_key in failed_keys:
                base_repeats += 1
            tg = gains.get(teacher)

            # masked choice: skip anything already failed for this objective
            offered = [(name, float(g)) for name, g in gains.items()]
            untried = [(n, g) for n, g in offered
                       if (otype, n) not in failed_keys]
            if teacher_key in failed_keys and untried:
                choice_name, choice_gain = max(untried, key=lambda ng: ng[1])
                substituted += 1
                if tg is not None:
                    sub_delta.append(choice_gain - float(tg))
            else:
                choice_name, choice_gain = teacher, tg

            # masked repeat: repeated only if teacher stayed AND it was a known
            # failure AND nothing untried was available
            if (otype, choice_name) in failed_keys:
                mask_repeats += 1

            og = max((g for _, g in offered), default=None)
            if tg is not None:
                base_gain.append(float(tg))
            if choice_gain is not None:
                mask_gain.append(float(choice_gain))
            elif tg is not None:
                mask_gain.append(float(tg))
            else:
                chosen_unknown_gain += 1
            if og is not None:
                oracle_gain.append(float(og))

            # update memory from this step's real outcome
            if r.get("advanced") is False:
                failed_keys.add(teacher_key)
            tried_keys.add(teacher_key)

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    n = total
    print("steps replayed:", n)
    print("repeat-failure rate  baseline: %5.1f%%  (%d)" % (100.0 * base_repeats / n, base_repeats))
    print("repeat-failure rate  masked  : %5.1f%%  (%d)" % (100.0 * mask_repeats / n, mask_repeats))
    print("substitutions fired:", substituted,
          "(%.1f%% of steps)" % (100.0 * substituted / n))
    print()
    print("realized gain per step (where the chosen tool's gain is known):")
    print("  baseline (teacher)   %.4f  (n=%d)" % (mean(base_gain), len(base_gain)))
    print("  masked   (episodic)  %.4f  (n=%d)" % (mean(mask_gain), len(mask_gain)))
    print("  oracle               %.4f  (n=%d)" % (mean(oracle_gain), len(oracle_gain)))
    print()
    print("net gain change on substituted steps: %+.4f mean (n=%d)"
          % (mean(sub_delta), len(sub_delta)))
    print()
    before = 100.0 * base_repeats / n
    after = 100.0 * mask_repeats / n
    print("RESULT: repeat-failure %.1f%% -> %.1f%% ; realized gain %.4f -> %.4f"
          % (before, after, mean(base_gain), mean(mask_gain)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())