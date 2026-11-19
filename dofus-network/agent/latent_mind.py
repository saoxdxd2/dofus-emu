"""Pure-NumPy runtime for the simplicial latent world model (Pillar 5).

The artifact is trained offline by `ai/world_model_latent.py` and ships as
`models/latent_world.npz`; this module never imports torch, jax or a network
client. It provides:

* the same encoder contract the trainer used -- 42 observation features, a
  four-slot ALER memory (128 numbers) and a 12-number slow state -> eight
  independent 8-way softmax blocks (SimNorm);
* the Gauss-Seidel slow-state update and a closed-form decay over elapsed time
  (the CfC prior's approximation);
* a K-member dynamics ensemble predicting the next latent, a symlog two-hot
  reward and a stall probability;
* real-time prediction surprise with a bounded diagonal precision, computed only
  when the observation is fresh;
* the System 1 / System 2 switch and, for System 2, a batched
  expected-free-energy rollout whose epistemic term is the locally matched
  pairwise-KL disagreement surrogate;
* the orthogonal-decay delta-rule fast weights, updated only by credited
  commitments.

The module is deliberately optional. `load_if_configured` returns None when the
artifact or NumPy is missing, and every caller treats None as "no advisor".
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = REPO_ROOT / "models" / "latent_world.npz"
ENABLE_ENV = "DOFUS_LATENT_MIND"

FEATURE_DIM = 42
LATENT_DIM = 64
BLOCKS = 8
BLOCK_WIDTH = 8
MEMORY_SLOTS = 4
MEMORY_WIDTH = 32
MEMORY_DIM = MEMORY_SLOTS * MEMORY_WIDTH
SLOW_DIM = 12
ACTION_DIM = 28
ARG_DIM = 8
REWARD_BINS = 51
SYMLOG_SCALE = 4.0
SLOW_PRECISION = 0.2


def _gelu(x: np.ndarray) -> np.ndarray:
    return 0.5 * x * (1.0 + np.tanh(0.7978845608028654 * (x + 0.044715 * x ** 3)))


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    shifted = x - x.max(axis=axis, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.maximum(exp.sum(axis=axis, keepdims=True), 1e-12)


def simnorm(pre: np.ndarray) -> np.ndarray:
    shaped = pre.reshape(pre.shape[:-1] + (BLOCKS, BLOCK_WIDTH))
    return _softmax(shaped, axis=-1).reshape(pre.shape)


def symlog(value: float) -> float:
    return math.copysign(math.log1p(abs(float(value))), float(value))


def symlog_inv(value: float) -> float:
    return math.copysign(math.expm1(abs(float(value))), float(value))


def two_hot_decode(probs: np.ndarray, scale: float = SYMLOG_SCALE) -> np.ndarray:
    centers = np.linspace(-scale, scale, probs.shape[-1])
    return np.copysign(np.expm1(np.abs(probs @ centers)), probs @ centers)


def arg_summary(args: Any) -> np.ndarray:
    out = np.zeros(ARG_DIM, dtype="float32")
    if not isinstance(args, dict):
        return out
    for index, key in enumerate(("map_id", "item_id", "npc_id", "monster_id",
                                 "quest_id", "cell", "x", "y")):
        value = args.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        out[index] = math.copysign(math.log1p(abs(number)) / 12.0, number)
    out = np.clip(out, -1.0, 1.0)
    if args.get("direction"):
        out[6] = 0.5
    return out


@dataclass
class EFEConfig:
    horizon: int = 4
    trajectories: int = 16          # per candidate; 4 candidates -> 64 total
    gamma: float = 0.9
    lambda_ep: float = 0.5
    lambda_stall: float = 0.3
    beta_prior: float = 0.05
    candidates: int = 4


@dataclass
class EFEInfo:
    surprise: float
    system: int
    gap: float = 0.0
    chosen: str = ""
    mean_g: Dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {"surprise": round(self.surprise, 5), "system": self.system,
                "chosen": self.chosen,
                "mean_g": {k: round(v, 5) for k, v in self.mean_g.items()},
                "gap": round(self.gap, 5),
                "latency_ms": round(self.latency_ms, 3)}


class FastWeights:
    """64 x 28 orthogonal-decay delta rule, credited outcomes only."""

    def __init__(self, decay: float = 0.05, rate: float = 0.02,
                 clip: float = 2.0) -> None:
        self.decay = float(decay)
        self.rate = float(rate)
        self.clip_value = float(clip)
        self.W = np.zeros((LATENT_DIM, ACTION_DIM), dtype="float32")
        self.updates = 0

    def update(self, z: np.ndarray, action_index: int, target: float) -> bool:
        if not (0 <= action_index < ACTION_DIM):
            return False
        z = np.asarray(z, dtype="float32").reshape(-1)[:LATENT_DIM]
        norm = float(np.linalg.norm(z))
        if norm <= 1e-8:
            return False
        z = z / norm
        prediction = float(z @ self.W[:, action_index])
        error = float(target) - prediction
        self.W = self.W - self.decay * (self.W - np.outer(z, z @ self.W))
        self.W[:, action_index] += self.rate * error * z
        np.clip(self.W, -self.clip_value, self.clip_value, out=self.W)
        self.updates += 1
        return True

    def score(self, z: np.ndarray) -> np.ndarray:
        z = np.asarray(z, dtype="float32").reshape(-1)[:LATENT_DIM]
        norm = float(np.linalg.norm(z))
        if norm <= 1e-8:
            return np.zeros(ACTION_DIM, dtype="float32")
        return z / norm @ self.W


class LatentMind:
    """One advisor instance per planner; all state is explicit for replay."""

    def __init__(self, weights: Dict[str, np.ndarray], tools: Sequence[str],
                 stats: Optional[Dict[str, float]] = None,
                 seed: int = 0) -> None:
        self.weights = weights
        self.tools = [str(t) for t in tools]
        self.tool_index = {name: i for i, name in enumerate(self.tools)}
        self.stats = dict(stats or {})
        self.rng = np.random.default_rng(seed)
        self.fast = FastWeights()
        # state
        self.memory = np.zeros((MEMORY_SLOTS, MEMORY_WIDTH), dtype="float32")
        self.slow = np.zeros(SLOW_DIM, dtype="float32")
        self.precision = np.full(LATENT_DIM, 1.0, dtype="float32")
        self.variance = np.full(LATENT_DIM, 1.0, dtype="float32")
        self.last_z: Optional[np.ndarray] = None
        self.last_tool = -1
        self.last_args: Dict[str, Any] = {}
        self.last_t = 0.0
        self.surprise_mean = float(self.stats.get("surprise_mean", 0.0))
        self.surprise_std = float(self.stats.get("surprise_std", 1.0))
        self.last_surprise = 0.0
        self.calls = 0
        self.system2_calls = 0

    # -- construction --------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None, seed: int = 0) -> Optional["LatentMind"]:
        path = Path(path or ARTIFACT)
        if not path.exists():
            return None
        try:
            with np.load(path, allow_pickle=False) as payload:
                weights = {key[2:]: np.asarray(payload[key], dtype="float32")
                           for key in payload.files if key.startswith("w/")}
                tools = [str(x) for x in payload["meta/tools"]]
                stats = {key[4:]: float(payload[key])
                         for key in payload.files if key.startswith("stat/")
                         and np.asarray(payload[key]).ndim == 0}
        except (OSError, ValueError, KeyError) as exc:
            return None
        if not weights:
            return None
        return cls(weights, tools, stats, seed=seed)

    @classmethod
    def from_random(cls, seed: int = 0, tools: Optional[Sequence[str]] = None) -> "LatentMind":
        """A same-shape model for shape/behavior tests, never for quality claims."""
        rng = np.random.default_rng(seed)
        hidden = 128
        weights: Dict[str, np.ndarray] = {}

        def dense(prefix: str, fan_in: int, fan_out: int) -> None:
            weights[prefix + ".weight"] = (
                rng.normal(0, 1.0 / math.sqrt(max(1, fan_in)), (fan_out, fan_in))
            ).astype("float32")
            weights[prefix + ".bias"] = np.zeros(fan_out, dtype="float32")

        enc_in = FEATURE_DIM + MEMORY_DIM + SLOW_DIM
        dense("encoder.0", enc_in, hidden)
        dense("encoder.2", hidden, hidden)
        dense("encoder.4", hidden, LATENT_DIM)
        dense("mu", FEATURE_DIM, SLOW_DIM)
        dense("memory_key", LATENT_DIM, MEMORY_SLOTS)
        dense("memory_value", LATENT_DIM, MEMORY_WIDTH)
        dense("memory_gate", LATENT_DIM, 1)
        for member in range(5):
            prefix = "dynamics.%d" % member
            dense(prefix + ".0", LATENT_DIM + ACTION_DIM + ARG_DIM, hidden)
            dense(prefix + ".2", hidden, hidden)
            dense(prefix + ".4", hidden, LATENT_DIM + REWARD_BINS + 1)
        dense("value.0", LATENT_DIM, hidden // 2)
        dense("value.2", hidden // 2, 1)
        weights["prior_log_rate"] = np.zeros(SLOW_DIM, dtype="float32")
        if tools is None:
            from agent.features import tool_registry

            tools = tool_registry()
        return cls(weights, tools)

    # -- primitives ----------------------------------------------------------
    def _dense(self, x: np.ndarray, prefix: str) -> np.ndarray:
        return x @ self.weights[prefix + ".weight"].T + self.weights[prefix + ".bias"]

    def _mlp(self, x: np.ndarray, prefix: str, layers: int = 3) -> np.ndarray:
        # Sequential indices 0, 2, 4 (Linear, GELU, Linear, GELU, Linear)
        for index in range(layers):
            x = self._dense(x, "%s.%d" % (prefix, index * 2))
            if index < layers - 1:
                x = _gelu(x)
        return x

    def encode(self, x: Sequence[float]) -> np.ndarray:
        vector = np.asarray(x, dtype="float32").reshape(-1)
        if vector.size != FEATURE_DIM:
            raise ValueError("feature vector is %d wide, expected %d"
                             % (vector.size, FEATURE_DIM))
        merged = np.concatenate([vector, self.memory.reshape(-1), self.slow])
        return simnorm(self._mlp(merged, "encoder")[None, :])[0]

    def predict(self, z: np.ndarray, action: int,
                args: Optional[Dict[str, Any]] = None
                ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Ensemble prediction: latents [K,64], reward probs [K,51], stall [K]."""
        action_vec = np.zeros(ACTION_DIM, dtype="float32")
        if 0 <= action < ACTION_DIM:
            action_vec[action] = 1.0
        merged = np.concatenate([np.asarray(z, "float32").reshape(-1),
                                 action_vec, arg_summary(args)])
        latents, rewards, stalls = [], [], []
        members = sorted({int(key.split(".")[1]) for key in self.weights
                          if key.startswith("dynamics.")})
        for member in members:
            out = self._mlp(merged[None, :], "dynamics.%d" % member)[0]
            latents.append(simnorm(out[:LATENT_DIM].copy()))
            rewards.append(_softmax(out[LATENT_DIM:LATENT_DIM + REWARD_BINS]))
            stalls.append(float(1.0 / (1.0 + math.exp(-out[-1]))))
        return np.stack(latents), np.stack(rewards), np.asarray(stalls)

    def value(self, z: np.ndarray) -> float:
        out = self._mlp(np.asarray(z, "float32").reshape(1, -1), "value", layers=2)
        return float(1.0 / (1.0 + math.exp(-out.reshape(-1)[0])))

    # -- state ---------------------------------------------------------------
    def _update_slow(self, x: np.ndarray) -> None:
        mu = np.tanh(self._dense(np.asarray(x, "float32").reshape(1, -1), "mu")[0])
        self.slow = ((self.slow + SLOW_PRECISION * mu) / (1.0 + SLOW_PRECISION)).astype("float32")

    def _prior_slow(self, dt_s: float) -> None:
        """CfC closed form over physical time: a learned-rate decay."""
        rate = np.log1p(np.exp(self.weights["prior_log_rate"]))  # softplus
        decay = np.exp(-rate * max(0.0, float(dt_s)))
        self.slow = (self.slow * decay).astype("float32")

    def _update_memory(self, z: np.ndarray) -> None:
        logits = self._dense(z[None, :], "memory_key")[0]
        slot = int(np.argmax(logits))
        value = np.tanh(self._dense(z[None, :], "memory_value")[0])
        gate = float(1.0 / (1.0 + math.exp(-self._dense(z[None, :], "memory_gate")[0, 0])))
        self.memory[slot] = (self.memory[slot]
                             + gate * (value - self.memory[slot])).astype("float32")

    def _freshness(self, x: np.ndarray) -> float:
        try:
            from agent.features import FEATURE_NAMES

            index = FEATURE_NAMES.index("obs_stale")
        except (ImportError, ValueError):
            return 1.0
        return 0.0 if float(x[index]) > 0.5 else 1.0

    def observe(self, x: Sequence[float], now: Optional[float] = None) -> float:
        """Encode a fresh observation and return the prediction surprise.

        The observation is masked when it is stale: no latent write, no
        precision update, surprise 0. That is the sensory-mask contract, not a
        confidence bonus.
        """
        vector = np.asarray(x, dtype="float32").reshape(-1)
        now = time.monotonic() if now is None else float(now)
        dt = max(0.0, now - self.last_t) if self.last_t else 0.0
        self._prior_slow(dt)
        self.last_t = now
        fresh = self._freshness(vector)
        self.calls += 1
        if fresh <= 0.0:
            self.last_surprise = 0.0
            return 0.0
        z = self.encode(vector)
        surprise = 0.0
        if self.last_z is not None and self.last_tool >= 0:
            latents, _, _ = self.predict(self.last_z, self.last_tool, self.last_args)
            predicted = latents.mean(axis=0)
            residual = np.asarray(z - predicted, dtype="float32")
            self.variance = 0.99 * self.variance + 0.01 * residual ** 2
            self.precision = np.clip(1.0 / (self.variance + 1e-6), 0.05, 20.0)
            surprise = float(0.5 * np.sum(self.precision * residual ** 2
                                          - np.log(self.precision)))
        self._update_memory(z)
        self._update_slow(vector)
        self.last_z = z
        self.last_surprise = surprise
        if self.stats.get("surprise_samples"):
            # Welford-free drift guard: keep the calibration usable when the live
            # distribution moves, but never let one outlier reset it.
            self.surprise_mean = 0.999 * self.surprise_mean + 0.001 * surprise
            self.surprise_std = 0.999 * self.surprise_std + 0.001 * abs(
                surprise - self.surprise_mean)
        return surprise

    def note_action(self, tool: str, args: Optional[Dict[str, Any]] = None) -> None:
        self.last_tool = self.tool_index.get(str(tool), -1)
        self.last_args = dict(args or {})

    # -- switching -----------------------------------------------------------
    def is_surprised(self) -> bool:
        return self.last_surprise > self.surprise_mean + 1.5 * max(1e-6, self.surprise_std)

    def choose_system(self, conformal_size: Optional[int]) -> int:
        """1 = act immediately, 2 = deliberate. Size 0 or >=2 forces System 2."""
        if conformal_size is not None and conformal_size != 1:
            return 2
        return 2 if self.is_surprised() else 1

    # -- System 2 ------------------------------------------------------------
    def _pairwise_kl(self, latents: np.ndarray) -> np.ndarray:
        """Locally matched pairwise-KL disagreement, shape [N]."""
        members, batch, dim = latents.shape
        blocks = latents.reshape(members, batch, BLOCKS, BLOCK_WIDTH)
        logs = np.log(np.maximum(blocks, 1e-12))
        # cross[b] = sum_k,j KL(k || j) per batch item
        total = np.zeros((batch,), dtype="float32")
        for block in range(BLOCKS):
            p = blocks[:, :, block, :]
            logp = logs[:, :, block, :]
            cross = -np.einsum("kbw,jbw->bkj", p, np.exp(logp)).sum(axis=-1)
            # More stable and cheaper: KL(p||q) = sum p log p - sum p log q
            entropy = (p * logp).sum(axis=-1)                 # [K,B]
            cross = cross - entropy.sum(axis=0)
            # cross is now sum_{k,j} (sum p_k log p_k - sum p_k log p_j)
            total += cross.astype("float32")
        return 0.5 * total / float(members * members)

    def rank(self, x: Sequence[float], probabilities: Sequence[float],
             conformal_size: Optional[int] = None,
             cfg: Optional[EFEConfig] = None) -> EFEInfo:
        """One decision: update surprise, switch, and on System 2 run EFE."""
        started = time.perf_counter()
        cfg = cfg or EFEConfig()
        self.observe(x)
        system = self.choose_system(conformal_size)
        if system == 1 or self.last_z is None:
            info = EFEInfo(surprise=self.last_surprise, system=1,
                           latency_ms=(time.perf_counter() - started) * 1000.0)
            return info
        self.system2_calls += 1
        probs = np.asarray(probabilities, dtype="float32").reshape(-1)
        if probs.size != ACTION_DIM:
            padded = np.zeros(ACTION_DIM, dtype="float32")
            padded[:min(probs.size, ACTION_DIM)] = probs[:ACTION_DIM]
            probs = padded
        probs = np.clip(probs, 1e-8, None)
        probs = probs / probs.sum()
        candidates = np.argsort(-probs)[:max(1, cfg.candidates)]
        mean_g: Dict[str, float] = {}
        for candidate in candidates:
            total = self._efe_for(candidate, probs, cfg)
            mean_g[self.tools[int(candidate)]] = float(total)
        chosen = min(mean_g, key=mean_g.get)
        ordered = sorted(mean_g.values())
        gap = float(ordered[1] - ordered[0]) if len(ordered) > 1 else 0.0
        return EFEInfo(surprise=self.last_surprise, system=2, gap=gap,
                       chosen=chosen, mean_g=mean_g,
                       latency_ms=(time.perf_counter() - started) * 1000.0)

    def _efe_for(self, action0: int, probs: np.ndarray, cfg: EFEConfig) -> float:
        horizon = max(1, cfg.horizon)
        batch = max(1, cfg.trajectories)
        z = np.repeat(self.last_z[None, :], batch, axis=0)
        total_g = np.zeros((batch,), dtype="float32")
        discount = 1.0
        for step in range(horizon):
            actions = (np.full((batch,), action0, dtype="int64") if step == 0
                       else self.rng.choice(ACTION_DIM, size=batch, p=probs))
            # One ensemble prediction per distinct action keeps the batch small.
            latents = np.zeros((batch, LATENT_DIM), dtype="float32")
            rewards = np.zeros((batch,), dtype="float32")
            stalls = np.zeros((batch,), dtype="float32")
            epistemic = np.zeros((batch,), dtype="float32")
            for action in np.unique(actions):
                where = np.where(actions == action)[0]
                means, reward_probs, stall_probs = self.predict(
                    z[where], int(action), {})
                latents[where] = means.mean(axis=0)
                rewards[where] = two_hot_decode(reward_probs, SYMLOG_SCALE).mean(axis=0)
                stalls[where] = stall_probs.mean(axis=0)
                epistemic[where] = self._pairwise_kl(means)
            total_g += discount * (-rewards - cfg.lambda_ep * epistemic
                                   + cfg.lambda_stall * stalls)
            z = latents
            discount *= cfg.gamma
        total_g -= (cfg.gamma ** horizon) * np.asarray(
            [self.value(row) for row in z], dtype="float32")
        total_g -= cfg.beta_prior * math.log(max(1e-8, float(probs[action0])))
        return float(total_g.mean())

    # -- learning from commitments ------------------------------------------
    def credit(self, tool: str, reward: float) -> bool:
        """Only a credited commitment reaches this method (w_learn == 1)."""
        if self.last_z is None:
            return False
        index = self.tool_index.get(str(tool), -1)
        if index < 0:
            return False
        return self.fast.update(self.last_z, index, float(reward))

    def fast_bonus(self) -> Optional[np.ndarray]:
        if self.last_z is None or self.fast.updates == 0:
            return None
        return self.fast.score(self.last_z)

    def stats_dict(self) -> Dict[str, Any]:
        return {"calls": self.calls, "system2": self.system2_calls,
                "surprise": round(self.last_surprise, 5),
                "surprise_mean": round(self.surprise_mean, 5),
                "surprise_std": round(self.surprise_std, 5),
                "fast_updates": self.fast.updates}

    def reset(self) -> None:
        self.memory[:] = 0.0
        self.slow[:] = 0.0
        self.precision[:] = 1.0
        self.variance[:] = 1.0
        self.last_z = None
        self.last_tool = -1
        self.last_t = 0.0
        self.last_surprise = 0.0


def load_if_configured(env: Optional[Dict[str, str]] = None,
                       path: Optional[Path] = None) -> Optional[LatentMind]:
    environ = env if env is not None else os.environ
    if str(environ.get(ENABLE_ENV, "0")).strip().lower() in ("0", "off", "false", "no", ""):
        return None
    return LatentMind.load(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """A tiny self-check usable without the artifact."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", default=str(ARTIFACT))
    parser.add_argument("--random", action="store_true")
    args = parser.parse_args(argv)
    mind = LatentMind.from_random(0) if args.random else LatentMind.load(Path(args.artifact))
    if mind is None:
        print("no artifact at %s; run ai/world_model_latent.py first" % args.artifact)
        return 1
    x = np.zeros(FEATURE_DIM, dtype="float32")
    for step in range(3):
        mind.note_action("nav.goto", {"map_id": 100 + step})
        print(step, "surprise", round(mind.observe(x + step * 0.01), 4))
    print(json.dumps(mind.stats_dict()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
