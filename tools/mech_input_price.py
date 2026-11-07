"""Does telling the network about mechanics buy wins? Two checkpoints, one difference: input.

This is task #142's actual question, stripped of the architecture. The mechanic-aware head was
requested because the target head could not route by spell (its cross-spell gap was a constant,
measured in \u00a7X.8), and the observation has since grown to carry what the client's own tables say a
cast does: five mechanic numbers and three summon numbers per spell, plus four swarm globals. But a
wider observation is not by itself a better policy, and the honest test is the ablation-of-the-input:

    arm A  trained on the swarm corpus seeing the 81-number actor token as built
    arm B  trained on the *same boards, same seed, same hyperparameters* with the twelve
           mechanic/summon/pool numbers zeroed in the observation only

`env.observe(..., blind=...)` zeroes those blocks; it does not touch the tables, because the tables
are the physics -- `step` spends them and `heuristic.py` prices an instant kill at 1e6, so a corpus
with the columns cleared is a different world, not a blinded network. Measured directly: clearing the
eight columns changed the scripted winners on 64 boards.

Both arms are then scored on untouched hold boards with the shipped ruler -- `make_eval_rollout`
greedy player against the heuristic monster side, the same call `wsp_program_target.py` uses for its
incumbent arm -- and the comparison is paired by board with the tool's own `paired()`.

What the numbers can say:
  delta > 0, CI excludes 0   mechanic input pays at this training budget
  CI includes 0              no measured effect; this budget does not establish that the input helps
  delta < 0, CI excludes 0   the wider token cost wins at this training budget

Read from the checkpoint, not from the command line: each payload records its own `blind` list, so an
evaluation that handed the blinded arm the mechanic features it never saw would be scoring a network
that does not exist.

    python tools/mech_input_price.py --visible <a.pkl> --blind <b.pkl> \
        --hold <encounters_boss_swarm_hold_s4_2048.npz> --boards 1024
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import pickle
import sys

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
sys.path.insert(0, str(BRAIN))

import jax                                                      # noqa: E402
import jax.numpy as jnp                                         # noqa: E402

from combat.encounter.env import Config, initial_state, observe  # noqa: E402
from combat.encounter.train import (make_baseline_rollout,        # noqa: E402
                                    make_eval_rollout)

_TOOLS = REPO / "tools" / "wsp_program_target.py"
_spec = importlib.util.spec_from_file_location("wsp_program_target", _TOOLS)
tool = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("wsp_program_target", tool)
_spec.loader.exec_module(tool)
paired = tool.paired

SKIP = ("level", "monster_id", "boss", "group", "_classes", "_party_classes")
EVIDENCE = pathlib.Path(os.environ.get(
    "DOFUS_EVIDENCE", pathlib.Path.home() / "Documents" / "dofus-evidence")) / "touch-data"


def load(path: pathlib.Path, boards: int, cfg: Config):
    z = np.load(path)
    arrays = {k: jnp.asarray(z[k][:boards]) for k in z.files
              if k not in SKIP and (np.issubdtype(z[k].dtype, np.number)
                                    or z[k].dtype == np.bool_)}
    tables = {k: v for k, v in arrays.items() if k.startswith("spell_")}
    spec = {k: v for k, v in arrays.items() if not k.startswith("spell_")}
    if spec["hp"].shape[1] != cfg.n_actors:
        raise SystemExit("%s has %d actor slots; --allies/%s asks for %d"
                         % (path.name, spec["hp"].shape[1], cfg.allies, cfg.n_actors))
    return spec, tables


def arm(path: str, spec, tables, cfg, blind_expected=None):
    """Score one checkpoint on these boards, with the blindness the checkpoint itself records."""
    art = pickle.loads(pathlib.Path(path).read_bytes())
    blind = tuple(art.get("blind") or ())
    # every check below runs before the parameters are moved: a refusal that has already built the
    # network is a refusal that cost the caller a minute and a GB of RAM for nothing
    if blind_expected is not None and blind != blind_expected:
        raise SystemExit("%s records blind=%s but the caller asked for %s -- the file is not the "
                         "arm being compared" % (pathlib.Path(path).name, blind, blind_expected))
    cfg_ck = Config(**{k: v for k, v in art["config"].items()})
    if cfg_ck != cfg:
        raise SystemExit("%s was trained with config %s, these boards use %s"
                         % (pathlib.Path(path).name, cfg_ck, cfg))
    params = jax.tree_util.tree_map(jnp.asarray, art["params"])
    _o, _g, _m = observe(initial_state(spec, tables, cfg), tables, cfg, blind=blind)
    want = art.get("feat_dim")
    if want is not None and want != int(_o.shape[-1]):
        raise SystemExit("%s was trained at feat_dim %s but these boards observe %d -- it cannot "
                         "read this corpus" % (pathlib.Path(path).name, want, int(_o.shape[-1])))
    length = 2 * cfg.max_rounds
    roll = jax.jit(make_eval_rollout(cfg, length, opponent="heuristic", player="greedy",
                                     blind=blind))
    wins = [np.asarray(roll(params, initial_state(spec, tables, cfg), tables,
                            jax.random.PRNGKey(int(s)))["winner"]) == 0
            for s in range(3)]
    return {"checkpoint": pathlib.Path(path).name, "blind": list(blind),
            "feat_dim": int(_o.shape[-1]), "iters": len(art.get("metrics") or []),
            "win_flags": np.mean(np.stack(wins), axis=0)}


def input_verdict(d):
    lower = d["delta"] + d["ci95"][0]
    upper = d["delta"] + d["ci95"][1]
    if lower > 0:
        return "mechanic input pays at this training budget"
    if upper < 0:
        return "mechanic input costs wins at this training budget"
    return ("no measurable effect of the mechanic input at this training budget -- "
            "this budget does not establish that the input helps")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--visible", required=True)
    ap.add_argument("--blind", required=True)
    ap.add_argument("--hold", default=str(EVIDENCE / "encounters_boss_swarm_hold_s4_2048.npz"))
    ap.add_argument("--boards", type=int, default=1024)
    ap.add_argument("--allies", type=int, default=None,
                    help="party size; default is what the corpus meta records")
    ap.add_argument("--max-rounds", type=int, default=24)
    args = ap.parse_args(argv)

    hold = pathlib.Path(args.hold)
    meta = json.loads(hold.with_suffix(".json").read_text(encoding="utf-8"))
    cfg = Config(allies=args.allies if args.allies is not None
                 else int(meta.get("allies") or 1),
                 enemies=int(meta["max_enemies"]), spells=int(meta["slots"]),
                 max_rounds=args.max_rounds)
    spec, tables = load(hold, args.boards, cfg)
    vis = arm(args.visible, spec, tables, cfg, blind_expected=())
    blind = arm(args.blind, spec, tables, cfg,
                blind_expected=("mechanics", "summons", "pool"))
    scripted = jax.jit(make_baseline_rollout(cfg, 2 * cfg.max_rounds))
    ref = np.asarray(scripted(initial_state(spec, tables, cfg), tables)["winner"]) == 0

    out = {
        "hold_corpus": hold.name, "boards": int(args.boards), "allies": cfg.allies,
        "observation_width": vis["feat_dim"],
        "arms": [{"arm": a["checkpoint"], "blind": a["blind"], "iters": a["iters"],
                  "win_rate": round(float(a["win_flags"].mean()), 6)}
                 for a in (vis, blind)],
        "scripted_reference_win_rate": round(float(ref.mean()), 6),
        "paired_visible_vs_blind": paired(vis["win_flags"], blind["win_flags"]),
        "paired_visible_vs_reference": paired(vis["win_flags"], ref),
        "paired_blind_vs_reference": paired(blind["win_flags"], ref),
        "boards_visible_wins_blind_loses": int(np.count_nonzero(
            (vis["win_flags"] > 0.5) & (blind["win_flags"] <= 0.5))),
        "boards_blind_wins_visible_loses": int(np.count_nonzero(
            (blind["win_flags"] > 0.5) & (vis["win_flags"] <= 0.5))),
    }
    out["verdict"] = input_verdict(out["paired_visible_vs_blind"])
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
