"""Distil beam first actions and final duel outcomes into the live encounter policy.

Cache observations, never student logits. Optional neural KL augments beam labels;
value always learns outcomes. Corpus JSON and checkpoint pickle are trusted local
inputs. Permanent combatant-disjoint splits and a fixed iteration budget keep the
final test set out of candidate selection.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import math
import pickle
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for root in (REPO_ROOT, REPO_ROOT.parent):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
CORPUS_VERSION = 2
COMPONENTS = ("move", "spell", "target")


def require_jax():
    from combat.encounter import backend
    if backend.BACKEND != "jax" or not backend.HAS_JAX:
        raise RuntimeError("training requires the JAX backend; numpy is inference-only "
                           "(backend=%s, import said: %s)"
                           % (backend.BACKEND, getattr(backend, "JAX_IMPORT_ERROR", None)))
    return backend.jax


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def first_action(board, plan):
    """Read only actions[0], including beam casts with an embedded walk."""
    if not plan.actions:
        return {"type": "pass"}
    first = plan.actions[0]
    if first.kind == "cast":
        # Match LivePolicy._emit for solver versions folding move into cast.
        if first.from_cell != board.my_cell:
            return {"type": "move", "cell": int(first.from_cell)}
        return {"type": "cast", "spellId": first.spell_id,
                "targetId": first.target_id, "cell": first.target_cell}
    if first.kind == "move":
        return {"type": "move", "cell": first.move_to}
    return {"type": first.kind}


def actor_outcome(winner, actor):
    return 0.0 if winner is None else (1.0 if winner == actor else -1.0)


def make_splits(rows, seed):
    """Group connected combatant IDs, so even reused duelists cannot leak."""
    parent = {}

    def root(ident):
        parent.setdefault(ident, ident)
        if parent[ident] != ident:
            parent[ident] = root(parent[ident])
        return parent[ident]

    matchups = {}
    for row in rows:
        ids = tuple(row["combatant_ids"])
        if len(ids) != 2 or ids[0] == ids[1]:
            raise ValueError("a matchup needs two distinct combatant row IDs")
        if row["matchup"] in matchups and matchups[row["matchup"]] != ids:
            raise ValueError("inconsistent matchup identity")
        matchups[row["matchup"]] = ids
        parent[root(ids[1])] = root(ids[0])
    groups = {}
    for key, ids in sorted(matchups.items()):
        groups.setdefault(root(ids[0]), []).append(key)
    groups = list(groups.values())
    random.Random(seed).shuffle(groups)
    if len(groups) < 3:
        raise ValueError("need at least three disjoint groups for train/validation/test")
    held = max(1, len(groups) // 5)
    slices = {"train": groups[:-2 * held], "validation": groups[-2 * held:-held],
              "test": groups[-held:]}
    return {name: {"matchups": sorted(k for group in part for k in group),
                   "combatant_ids": sorted({i for group in part for k in group
                                             for i in matchups[k]})}
            for name, part in slices.items()}


def harvest(games, seed, beam_width=8, max_actions=2, plan_k=6, data_path=None):
    import numpy as np
    from combat import duel as du
    from combat.battle_solver import BattleSolver

    solver = BattleSolver(beam_width=beam_width, max_actions=max_actions)
    cells = du.load_map(du.DEFAULT_MAP)
    rows, duels, discarded = [], [], Counter()
    data_path = Path(data_path or du.DEFAULT_DATA).resolve()
    with np.load(data_path) as data:
        count = int(data["hp"].shape[0])
        if games * 2 > count:
            raise ValueError(f"{games} unique matchups need more than {count} combatants")
        picks = random.Random(seed).sample(range(count), games * 2)
        for index in range(games):
            ids = picks[2 * index:2 * index + 2]
            key = f"{ids[0]}-{ids[1]}"
            rng = random.Random(seed * 100003 + index * 7)
            state = du.make_duel(data, *ids, cells, rng=rng)
            if state is None:
                discarded["unbuildable_duel"] += 1
                continue
            pending = []

            def record(state, actor, plans, rng, model=None):
                plan = plans[0]
                board = du.board_for(state, actor)
                board.round = state.round
                pending.append({
                    "matchup": key, "combatant_ids": ids, "actor_id": actor.id,
                    "payload": board.to_fight_state(),
                    "first_action": plan.actions[0].as_dict() if plan.actions else None,
                    "action": first_action(board, plan),
                })
                return plan

            # Engine failures abort rather than label incomplete games as draws.
            final, _ = du.play_game(state, {1: record, 2: record}, solver, rng, k=plan_k)
            if not final.done:
                raise RuntimeError(f"duel {key} did not terminate")
            for row in pending:
                row["winner_id"] = final.winner
                row["outcome"] = actor_outcome(final.winner, row["actor_id"])
            rows.extend(pending)
            duels.append({"matchup": key, "combatant_ids": ids,
                          "winner_id": final.winner, "turns": final.turns,
                          "rounds": final.round,
                          "final_hp": {str(f.id): f.hp for f in final.fighters}})
    return {"version": CORPUS_VERSION, "rows": rows, "duels": duels,
            "manifest": {"seed": seed, "requested_games": games,
                         "teacher": {"type": "beam", "beam_width": beam_width,
                                     "max_actions": max_actions, "plan_k": plan_k,
                                     "max_nodes": solver.max_nodes},
                         "sources": {"data": str(data_path),
                                     "data_sha256": sha256(data_path),
                                     "map": str(du.DEFAULT_MAP),
                                     "map_sha256": sha256(du.DEFAULT_MAP)},
                         "discarded_label_reasons": dict(discarded),
                         "splits": make_splits(rows, seed)}}


def validate_corpus(corpus):
    if corpus.get("version") != CORPUS_VERSION:
        raise ValueError("unsupported corpus: reharvest first actions and final outcomes")
    rows = corpus["rows"]
    if not rows:
        raise ValueError("empty corpus")
    splits = corpus["manifest"]["splits"]
    if set(splits) != {"train", "validation", "test"}:
        raise ValueError("expected train/validation/test splits")
    seen_ids, seen_keys = set(), set()
    for name in ("train", "validation", "test"):
        ids, keys = set(splits[name]["combatant_ids"]), set(splits[name]["matchups"])
        if not keys or not ids or seen_ids & ids or seen_keys & keys:
            raise ValueError("empty or overlapping combatant-disjoint splits")
        actual_ids = {i for row in rows if row["matchup"] in keys for i in row["combatant_ids"]}
        actual_keys = {row["matchup"] for row in rows if row["matchup"] in keys}
        if ids != actual_ids or keys != actual_keys:
            raise ValueError("split manifest does not match corpus rows")
        seen_ids.update(ids)
        seen_keys.update(keys)
    if seen_keys != {row["matchup"] for row in rows}:
        raise ValueError("corpus has unsplit matchups")
    identities, winners = {}, {}
    for row in rows:
        key, ids = row["matchup"], tuple(row["combatant_ids"])
        if len(ids) != 2 or ids[0] == ids[1] or identities.setdefault(key, ids) != ids:
            raise ValueError("inconsistent matchup identity")
        winner = row["winner_id"]
        if row["actor_id"] not in (1, 2) or winner not in (None, 1, 2):
            raise ValueError("invalid duel actor/winner")
        if winners.setdefault(key, winner) != winner:
            raise ValueError("inconsistent final outcome within a duel")
        if row["actor_id"] != row["payload"]["my_id"]:
            raise ValueError("outcome perspective differs from observation actor")
        if row["outcome"] != actor_outcome(winner, row["actor_id"]):
            raise ValueError("outcome must be the final duel result from actor perspective")
        if not isinstance(row["action"], dict) or "type" not in row["action"]:
            raise ValueError("missing first-action label")


def save_corpus(path, corpus):
    validate_corpus(corpus)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(corpus, stream, allow_nan=False)


def board_inputs(policy, board):
    """The exact state/observe arithmetic of LivePolicy._forward_bundle."""
    import numpy as np
    from combat.encounter.backend import XP, at
    from combat.encounter.env import action_masks, initial_state, observe
    from combat.plan_value import board_encounter

    slots, player_rows = policy._bind_slots(board)
    targets = policy._bind_targets(board)
    tables, spec = board_encounter(board, policy._current_plan(board), policy.cfg,
                                   first_team=0, player_rows=player_rows, as_jax=False)
    state = initial_state(spec, tables, policy.cfg)
    state["ap"] = at(state["ap"])[:, 0].set(float(max(0, board.my_ap)))
    state["mp"] = at(state["mp"])[:, 0].set(float(max(0, board.my_mp)))
    state["round"] = XP.asarray([int(board.round or 0)], dtype=XP.int32)
    masks = action_masks(state, tables, policy.cfg)
    obs, glob, attention = observe(state, tables, policy.cfg,
                                   acting_team=XP.zeros((1,), dtype=XP.int32))
    inputs = {"obs": np.asarray(obs[0]), "glob": np.asarray(glob[0]),
              "attention": np.asarray(attention[0])}
    masks = {k: np.asarray(masks[k][0, 0]) for k in COMPONENTS}
    return inputs, masks, slots, targets


def action_label(policy, board, action, masks, slots, targets):
    """Unrepresentable actions have zero hard-label weights, not a pass label."""
    labels = {"move": 1, "spell": policy.cfg.spells, "target": 0,
              "move_weight": 0.0, "spell_weight": 0.0, "target_weight": 0.0}
    kind, reasons = action["type"], []
    if kind in ("pass", "hold"):
        move, spell, target = 1, policy.cfg.spells, 0
    elif kind == "move":
        reference = policy._reference_target(board, targets[0])
        directions = []
        for direction in (0, 2):
            walk = policy._walk_action(board, reference, direction)
            if walk is not None and walk["cell"] == action.get("cell"):
                directions.append(direction)
        if not directions:
            return labels, ["unrepresentable_move"]
        move, spell, target = directions[0], policy.cfg.spells, 0
    elif kind == "cast":
        spell = next((i for i, slot in enumerate(slots)
                      if not slot.get("empty") and slot["spell_id"] == action.get("spellId")), None)
        target = next((i for i, entry in enumerate(targets)
                       if entry["fighter_id"] is not None
                       and entry["fighter_id"] == action.get("targetId")), None)
        if spell is None:
            reasons.append("unbound_spell_id")
        if target is None:
            reasons.append("unbound_target_id")
        if reasons:
            return labels, reasons
        move = 1  # Placeholder: immediate casting says nothing about later movement.
    else:
        return labels, ["unsupported_action:" + str(kind)]
    if kind != "cast" and not masks["move"][move]:
        reasons.append("illegal_move_mask")
    if not masks["spell"][spell]:
        reasons.append("illegal_spell_mask")
    if not masks["target"][spell, target]:
        reasons.append("illegal_target_mask")
    if reasons:
        return labels, reasons
    if kind == "cast":
        direct = policy._direct_cast(board, slots[spell], targets[target])
        if direct is None or (action.get("cell") is not None and direct["cell"] != action["cell"]):
            return labels, ["unrepresentable_direct_cast"]
    labels.update(move=move, spell=spell, target=target,
                  move_weight=float(kind != "cast"), spell_weight=1.0,
                  target_weight=float(kind == "cast"))
    return labels, []


def prepare_dataset(policy, rows):
    import numpy as np
    from combat.battle_solver import Board

    dataset, discarded = [], Counter()
    for row in rows:
        try:
            board = Board.from_fight_state(row["payload"])
            if board.me is None or not board.me.alive:
                discarded["unrepresentable_input_actor"] += 1
                continue
            if len(board.enemies) > policy.cfg.enemies or len(board.allies) != 1:
                discarded["unrepresentable_input_actor_count"] += 1
                continue
            inputs, masks, slots, targets = board_inputs(policy, board)
            if not all(np.isfinite(value).all() for value in inputs.values()):
                discarded["nonfinite_input"] += 1
                continue
            label, reasons = action_label(policy, board, row["action"], masks, slots, targets)
            discarded.update(reasons)
            dataset.append({**inputs, **label, **{k + "_mask": v for k, v in masks.items()},
                            "outcome": float(row["outcome"])})
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise ValueError(f"invalid board in matchup {row['matchup']}: {exc}") from exc
    if not dataset:
        raise ValueError("split has no representable input boards")
    return {k: np.stack([row[k] for row in dataset]) for k in dataset[0]}, dict(discarded)


def validate_checkpoint(policy, payload):
    """LivePolicy fixes four spell slots and model.forward's four attention heads."""
    import numpy as np
    from combat.encounter import model
    from combat.encounter.backend import XP

    cfg = policy.cfg
    if cfg.allies != 1 or cfg.spells != 4 or cfg.enemies < 1:
        raise ValueError("checkpoint config is incompatible with live slot binding")
    if min(cfg.max_rounds, cfg.max_dist, cfg.casts_per_turn) <= 0:
        raise ValueError("invalid checkpoint config")
    feat, glob = 13 + 9 * cfg.spells, 9
    for key, value in {"feat_dim": feat, "global_dim": glob, "heads": 4}.items():
        if key in payload and payload[key] != value:
            raise ValueError(f"incompatible checkpoint {key}: expected {value}")
    if policy.params["enc1"][0].shape[0] != feat or policy.params["glob"][0].shape[0] != glob:
        raise ValueError("incompatible checkpoint observation dimensions")
    output = model.forward(policy.params, XP.zeros((1, cfg.n_actors, feat)),
                           XP.zeros((1, glob)), XP.ones((1, cfg.n_actors), dtype=bool))
    shapes = {"move": (1, cfg.n_actors, 3), "spell": (1, cfg.n_actors, cfg.n_spells),
              "target": (1, cfg.n_actors, cfg.n_spells, cfg.n_actors), "value": (1,)}
    if any(output[k].shape != shape for k, shape in shapes.items()):
        raise ValueError("incompatible checkpoint action dimensions")
    if not all(np.isfinite(np.asarray(v)).all() for v in output.values()):
        raise ValueError("nonfinite checkpoint forward output")


def masked_log_probs(logits, mask, temperature=1.0):
    import jax
    import jax.numpy as jnp
    # Unused, fully masked target rows still need finite softmax arithmetic.
    safe = mask | (~jnp.any(mask, axis=-1, keepdims=True)
                   & (jnp.arange(mask.shape[-1]) == 0))
    return jax.nn.log_softmax(jnp.where(safe, logits / temperature, -1.0e9), axis=-1)


def make_loss(teacher_params=None, temperature=1.0):
    require_jax()
    import jax
    import jax.numpy as jnp
    from combat.encounter import model

    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("temperature must be positive and finite")

    def loss(params, batch):
        # Keep this INSIDE autodiff. The cached-logit mutation test guards it.
        logits = model.forward(params, batch["obs"], batch["glob"], batch["attention"])
        scores = {k: logits[k][:, 0] for k in COMPONENTS}
        metrics = {}
        indices = jnp.arange(batch["obs"].shape[0])
        for component in COMPONENTS:
            raw, mask = scores[component], batch[component + "_mask"]
            weight = batch[component + "_weight"]
            if component == "target":
                raw, mask = raw[indices, batch["spell"]], mask[indices, batch["spell"]]
                weight = weight * (batch["spell"] < scores["spell"].shape[-1] - 1)
            lp = masked_log_probs(raw, mask)
            nll = -lp[indices, batch[component]]
            denom = jnp.maximum(jnp.sum(weight), 1.0)
            metrics[component + "_ce"] = jnp.sum(nll * weight) / denom
            correct = jnp.argmax(jnp.where(mask, raw, -1.0e9), axis=-1) == batch[component]
            metrics[component + "_accuracy"] = jnp.sum(correct * weight) / denom
        metrics["value_mse"] = jnp.mean(jnp.square(logits["value"] - batch["outcome"]))
        if teacher_params is not None:
            teacher = jax.tree_util.tree_map(jax.lax.stop_gradient, model.forward(
                teacher_params, batch["obs"], batch["glob"], batch["attention"]))
            teacher_spell = None
            for component in COMPONENTS:
                mask = batch[component + "_mask"]
                tlp = masked_log_probs(teacher[component][:, 0], mask, temperature)
                slp = masked_log_probs(scores[component], mask, temperature)
                prob = jnp.where(mask, jnp.exp(tlp), 0.0)
                kl = jnp.sum(prob * (tlp - slp), axis=-1)
                if component == "spell":
                    teacher_spell = prob
                if component == "target":
                    # E_teacher(spell)[KL(target | spell)], excluding pass.
                    kl = jnp.sum(teacher_spell[:, :-1] * kl[:, :-1], axis=-1)
                metrics[component + "_kl"] = jnp.mean(kl) * temperature ** 2
        total = sum(value for name, value in metrics.items() if not name.endswith("_accuracy"))
        return total, metrics
    return loss


def gradient_stats(grads):
    import numpy as np
    jax = require_jax()

    def norm(tree):
        arrays = [np.asarray(v, dtype=np.float64) for v in jax.tree_util.tree_leaves(tree)]
        if not all(np.isfinite(v).all() for v in arrays):
            raise RuntimeError("nonfinite gradients")
        return float(np.sqrt(sum(np.sum(v * v) for v in arrays)))

    total = norm(grads)
    if not math.isfinite(total) or total == 0:
        raise RuntimeError("zero or nonfinite overall gradients: forward may be frozen/cached")
    groups = {"trunk": [v for k, v in grads.items()
                         if k not in ("move", "spell", "t_q", "t_k", "t_bias", "value1", "value2")],
              "move": grads["move"], "spell": grads["spell"],
              "target": [grads[k] for k in ("t_q", "t_k", "t_bias")],
              "value": [grads[k] for k in ("value1", "value2")]}
    return {"norm": total, "group_norms": {k: norm(v) for k, v in groups.items()}}


def changed_leaves(before, after):
    import numpy as np
    jax = require_jax()
    a, b = jax.tree_util.tree_leaves(before), jax.tree_util.tree_leaves(after)
    if not all(np.isfinite(np.asarray(v)).all() for v in b):
        raise RuntimeError("nonfinite updated parameters")
    count = sum(not np.array_equal(np.asarray(x), np.asarray(y)) for x, y in zip(a, b))
    if count == 0:
        raise RuntimeError("no parameter change; refusing to save an unchanged checkpoint")
    return count


def fit(params, dataset, *, iters, batch_size, lr, seed, teacher_params=None, temperature=1.0):
    jax = require_jax()
    import jax.numpy as jnp
    from combat.encounter.train import adam_init, adam_update
    if iters <= 0 or batch_size <= 0 or not math.isfinite(lr) or lr <= 0:
        raise ValueError("iters, batch and lr must be positive and finite")
    grad = jax.jit(jax.value_and_grad(make_loss(teacher_params, temperature), has_aux=True))
    update = jax.jit(adam_update)
    opt, initial = adam_init(params), params
    rng = random.Random(seed)
    size, history = len(dataset["outcome"]), []
    for iteration in range(1, iters + 1):
        indices = rng.sample(range(size), min(batch_size, size))
        batch = {k: jnp.asarray(v[indices]) for k, v in dataset.items()}
        (value, _), grads = grad(params, batch)
        if not math.isfinite(float(value)):
            raise RuntimeError("nonfinite training loss")
        stats = gradient_stats(grads)
        previous = params
        params, opt = update(params, grads, opt, lr=lr)
        stats.update(iteration=iteration, loss=float(value),
                     changed_leaves=changed_leaves(previous, params))
        history.append(stats)
        if iteration == 1 or iteration % 50 == 0 or iteration == iters:
            print(json.dumps(stats, sort_keys=True))
    return params, {"steps": history, "changed_leaves": changed_leaves(initial, params),
                    "total_leaves": len(jax.tree_util.tree_leaves(params))}


def evaluate(params, dataset, batch_size=64, teacher_params=None, temperature=1.0):
    jax = require_jax()
    import jax.numpy as jnp
    import numpy as np
    loss = jax.jit(make_loss(teacher_params, temperature))
    sums, counts = Counter(), Counter()
    size = len(dataset["outcome"])
    for start in range(0, size, batch_size):
        batch = {k: jnp.asarray(v[start:start + batch_size]) for k, v in dataset.items()}
        _, metrics = loss(params, batch)
        for name, value in metrics.items():
            component = name.split("_")[0]
            weight = (float(np.asarray(batch[component + "_weight"]).sum())
                      if name.endswith(("_ce", "_accuracy")) else len(batch["outcome"]))
            if not math.isfinite(float(value)):
                raise RuntimeError("nonfinite evaluation metric")
            sums[name] += float(value) * weight
            counts[name] += weight
    result = {name: (sums[name] / counts[name] if counts[name] else None) for name in sums}
    result["boards"] = size
    result["supervised_counts"] = {k: int(dataset[k + "_weight"].sum()) for k in COMPONENTS}
    return result


def safe_paths(checkpoint, teacher, corpus, out):
    protected = [Path(checkpoint).resolve()]
    if teacher:
        protected.append(Path(teacher).resolve())
    corpus, out = Path(corpus).resolve(), Path(out).resolve()

    def same(a, b):
        return a == b or (a.exists() and b.exists() and a.samefile(b))

    if same(corpus, out) or any(same(p, dest) for p in protected for dest in (corpus, out)):
        raise ValueError("output and corpus must be separate from student init and teacher")
    if out.exists():
        raise FileExistsError(f"refusing to overwrite output: {out}")


def save_checkpoint(path, source_payload, params, manifest):
    import numpy as np
    jax = require_jax()
    changed_leaves(source_payload["params"], params)
    payload = dict(source_payload)
    payload["params"] = jax.tree_util.tree_map(np.asarray, params)
    payload["distillation"] = manifest
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=120)
    parser.add_argument("--data", type=Path, help="encounter table reserved for corpus generation")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--iters", type=int, default=400)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-3)
    parser.add_argument("--checkpoint", required=True, help="explicit student initialization")
    parser.add_argument("--teacher-checkpoint", help="optional neural teacher for legal-distribution KL")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--corpus", help="read existing JSON or persist harvest (default: OUT.corpus.json)")
    parser.add_argument("--out", default=str(REPO_ROOT / "models" / "encounter_distilled.pkl"))
    parser.add_argument("--harvest-only", action="store_true")
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--max-actions", type=int, default=2)
    parser.add_argument("--plan-k", type=int, default=6)
    args = parser.parse_args(argv)
    if min(args.games, args.iters, args.batch, args.beam_width, args.plan_k) <= 0 or args.max_actions < 0:
        parser.error("games/iters/batch/beam-width/plan-k must be positive; max-actions nonnegative")
    if not all(math.isfinite(x) and x > 0 for x in (args.lr, args.temperature)):
        parser.error("lr and temperature must be positive and finite")
    checkpoint = Path(args.checkpoint).resolve()
    teacher_path = Path(args.teacher_checkpoint).resolve() if args.teacher_checkpoint else None
    out = Path(args.out).resolve()
    corpus_path = Path(args.corpus).resolve() if args.corpus else out.with_suffix(".corpus.json")
    safe_paths(checkpoint, teacher_path, corpus_path, out)
    if not args.harvest_only:
        require_jax()  # Reject inference-only installations before any harvest/write.
    from combat.live_policy import LivePolicy
    source_payload = pickle.loads(checkpoint.read_bytes())
    policy = LivePolicy(checkpoint)
    validate_checkpoint(policy, source_payload)
    source = {"path": str(checkpoint), "sha256": sha256(checkpoint)}
    teacher, neural = None, None
    if teacher_path:
        teacher_payload = pickle.loads(teacher_path.read_bytes())
        teacher = LivePolicy(teacher_path)
        validate_checkpoint(teacher, teacher_payload)
        if teacher.cfg != policy.cfg:
            raise ValueError("teacher and student observation/action configs differ")
        neural = {"type": "neural", "path": str(teacher_path), "sha256": sha256(teacher_path),
                  "temperature": args.temperature, "config": asdict(teacher.cfg)}
    if corpus_path.exists():
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        validate_corpus(corpus)
        if args.data and corpus["manifest"]["sources"]["data_sha256"] != sha256(args.data):
            raise ValueError("saved corpus belongs to a different encounter table")
    else:
        corpus = harvest(args.games, args.seed, args.beam_width, args.max_actions, args.plan_k, args.data)
        corpus["manifest"]["source_checkpoint"] = source
        save_corpus(corpus_path, corpus)
    print(f"corpus: {corpus_path} ({len(corpus['rows'])} boards)")
    if args.harvest_only:
        return 0
    datasets, discarded = {}, {}
    for name, split in corpus["manifest"]["splits"].items():
        keys = set(split["matchups"])
        rows = [row for row in corpus["rows"] if row["matchup"] in keys]
        datasets[name], discarded[name] = prepare_dataset(policy, rows)
    teacher_params = teacher.params if teacher else None
    params, diagnostics = fit(policy.params, datasets["train"], iters=args.iters,
                              batch_size=args.batch, lr=args.lr, seed=args.seed,
                              teacher_params=teacher_params, temperature=args.temperature)
    # Fixed final iteration; no candidate selection reads either held-out split.
    metrics = {name: evaluate(params, datasets[name], args.batch, teacher_params, args.temperature)
               for name in ("train", "validation", "test")}
    manifest = {"source_checkpoint": source, "corpus": str(corpus_path),
                "corpus_sha256": sha256(corpus_path), "corpus_manifest": corpus["manifest"],
                "splits": corpus["manifest"]["splits"], "beam_teacher": corpus["manifest"]["teacher"],
                "neural_teacher": neural, "config": asdict(policy.cfg),
                "discarded_label_reasons": discarded, "gradients": diagnostics,
                "metrics": metrics, "selection": "fixed_final_iteration_no_selection",
                "objective": "masked beam CE + outcome value MSE + optional temperature-squared neural KL",
                "value_target": "final duel outcome: actor win +1, loss -1, draw 0",
                "neural_supervision": "all representable input boards; target KL conditioned on real spell",
                "iters": args.iters, "batch": args.batch, "lr": args.lr, "seed": args.seed}
    save_checkpoint(out, source_payload, params, manifest)
    print(json.dumps({"out": str(out), "heldout": metrics["test"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
