"""Continuous, differentiable routing mechanisms for decision/routing/heuristic code.

Implements the four mechanisms from AI_ARCHITECTURE.md §0 that replace discrete
``if/else`` branches in Decision, Routing & Heuristic code:

1. **Soft MoE / Gated Attention Routing** -- ``soft_route`` replaces
   ``if cond: branch_A else: branch_B`` with a learned gating distribution.
2. **Cross-Attention + Sink Token (k_0)** -- ``cross_attend_with_sink`` replaces
   discrete entity-matching if/else trees with Query-Key attention over entities
   plus a learnable null/sink key.
3. **Precision-Weighted Interpolation** -- ``precision_blend`` replaces the
   brittle ``if surprise < tau: fast() else: slow()`` threshold with a smooth
   blend of fast prior and lookahead value.
4. **Learned Termination Gates** -- ``termination_prob`` replaces stateful
   ``if streak >= 3`` counters with a continuous Option-Critic termination
   probability.

All functions are pure NumPy forward passes (no autograd, no torch). They are
deliberately small and dependency-free so they can run inside the existing
inference path and later be trained with any gradient source.

Safety / Legality if/else (``agent/schema.py:validate_call``, ``_should_stop``,
``_farm_allowed``, kick-signature guards, BattleSolver castability) is NOT
touched by this module -- those stay as hard boolean masks.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Numerical helpers
# ---------------------------------------------------------------------------

def softmax(x: np.ndarray, axis: int = -1, tau: float = 1.0) -> np.ndarray:
    """Numerically stable softmax with temperature."""
    x = np.asarray(x, dtype=np.float64) / max(tau, 1e-8)
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=axis, keepdims=True)


def sigmoid(x: np.ndarray) -> np.ndarray:
    """Element-wise logistic sigmoid."""
    x = np.asarray(x, dtype=np.float64)
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))


# ---------------------------------------------------------------------------
# 1. Soft MoE / Gated Attention Routing
# ---------------------------------------------------------------------------

class Expert:
    """One expert in a Soft MoE: a weight matrix plus a callable output."""

    def __init__(self, weight: np.ndarray, compute: Callable[[np.ndarray], np.ndarray]) -> None:
        self.weight = np.asarray(weight, dtype=np.float64)
        self.compute = compute

    def gate_logit(self, z: np.ndarray) -> float:
        return float(self.weight @ np.asarray(z, dtype=np.float64))

    def __call__(self, z: np.ndarray) -> np.ndarray:
        return self.compute(np.asarray(z, dtype=np.float64))


def soft_route(
    z: np.ndarray,
    experts: Sequence[Expert],
    tau: float = 1.0,
    straight_through: bool = False,
) -> np.ndarray:
    """Soft MoE routing: ``g(z) = softmax(W_g * z / tau)``, ``y = sum(g_m * f_m(z))``.

    Parameters
    ----------
    z:
        Input state vector.
    experts:
        Sequence of :class:`Expert` objects. Each contributes a gate logit
        ``weight @ z`` and an output ``compute(z)``.
    tau:
        Temperature. Low tau -> near-discrete argmax routing; high tau ->
        uniform blending. At CPU inference time, ``straight_through=True`` pays
        compute for only the winning expert while keeping the forward pass
        differentiable in training.
    straight_through:
        If True, route to the single highest-gate expert (argmax) at inference
        while keeping soft gates for the gradient in training.

    Returns
    -------
    The gated mixture of expert outputs.
    """
    z = np.asarray(z, dtype=np.float64).ravel()
    logits = np.array([e.gate_logit(z) for e in experts], dtype=np.float64)
    gates = softmax(logits, axis=-1, tau=tau)

    if straight_through:
        winner = int(np.argmax(logits))
        return experts[winner](z)

    outputs = np.stack([e(z) for e in experts], axis=0)  # [M, out_dim]
    return (gates[:, None] * outputs).sum(axis=0)


# ---------------------------------------------------------------------------
# 2. Cross-Attention + Sink Token (k_0)
# ---------------------------------------------------------------------------

def cross_attend_with_sink(
    queries: np.ndarray,
    keys: np.ndarray,
    values: np.ndarray,
    sink_key: np.ndarray,
    sink_value: np.ndarray,
    scale: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Query-Key cross-attention with a learnable null/sink key ``k_0``.

    Attention logits are computed over real keys plus one sink key. When no real
    entity matches the query, attention mass collapses onto the sink token
    (``alpha[:, 0] -> 1``), outputting ``v_null``. This replaces discrete
    ``if found: ... else: ...`` entity-lookup trees.

    Parameters
    ----------
    queries:
        ``[Q, D]`` query vectors.
    keys:
        ``[L, D]`` real entity keys.
    values:
        ``[L, V]`` real entity values.
    sink_key:
        ``[D]`` learnable null key.
    sink_value:
        ``[V]`` learnable null value.
    scale:
        Attention scale ``1/sqrt(d_k)``. Defaults to ``1/sqrt(D)``.

    Returns
    -------
    ``(context, attention)`` where ``context`` is ``[Q, V]`` and ``attention``
    is ``[Q, L+1]`` (last column is sink mass).
    """
    queries = np.asarray(queries, dtype=np.float64)
    keys = np.asarray(keys, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    sink_key = np.asarray(sink_key, dtype=np.float64).ravel()
    sink_value = np.asarray(sink_value, dtype=np.float64).ravel()

    if scale is None:
        scale = 1.0 / np.sqrt(keys.shape[1])

    # Real-key logits: [Q, L]
    real_logits = (queries @ keys.T) * scale
    # Sink logits: [Q, 1]
    sink_logits = (queries @ sink_key) * scale
    sink_logits = sink_logits.reshape(-1, 1)

    # Concatenate sink as first column: [Q, L+1]
    logits = np.concatenate([sink_logits, real_logits], axis=1)
    attention = softmax(logits, axis=-1)

    sink_mass = attention[:, 0:1]  # [Q, 1]
    real_mass = attention[:, 1:]   # [Q, L]

    context = real_mass @ values + sink_mass * sink_value[None, :]  # [Q, V]
    return context, attention


# ---------------------------------------------------------------------------
# 3. Precision-Weighted Interpolation (System 1 / System 2)
# ---------------------------------------------------------------------------

def precision_blend(
    fast_logits: np.ndarray,
    slow_logits: np.ndarray,
    confidence: np.ndarray,
) -> np.ndarray:
    """Blend fast prior logits and lookahead/EFE value continuously.

    ``ell_final = (1 - rho) * ell_S1 + rho * ell_S2`` where ``rho`` is the
    epistemic confidence weight. No threshold branch -- the blend is smooth.

    A compute-budget optimization can still gate on ``rho`` (skip System 2 on
    easy steps), but that is purely an optimization, not a logic branch.
    """
    fast = np.asarray(fast_logits, dtype=np.float64)
    slow = np.asarray(slow_logits, dtype=np.float64)
    rho = np.asarray(confidence, dtype=np.float64)
    return (1.0 - rho) * fast + rho * slow


def pondernet_halting_probs(
    halting_logits: Sequence[float],
) -> np.ndarray:
    """PonderNet / ACT geometric halting probabilities.

    At each thinking step ``n = 1 .. N_max``, emit ``lambda_n = sigma(h_n)`` so
    the expected representation is a smooth sum ``y = sum(p_n * y_n)`` with
    ``p_n = lambda_n * prod_{j < n} (1 - lambda_j)``.

    Returns halting probabilities ``p_n`` of length ``N_max`` (sums to <= 1;
    the remainder is the "did not halt within budget" mass).
    """
    lambdas = sigmoid(np.asarray(halting_logits, dtype=np.float64))
    n = len(lambdas)
    probs = np.empty(n, dtype=np.float64)
    survival = 1.0
    for i in range(n):
        probs[i] = lambdas[i] * survival
        survival *= (1.0 - lambdas[i])
    return probs


# ---------------------------------------------------------------------------
# 4. Learned Termination Gates & ALER Slot Memory
# ---------------------------------------------------------------------------

def termination_prob(
    z: np.ndarray,
    omega: np.ndarray,
    expert_embedding: Optional[np.ndarray] = None,
) -> float:
    """Option-Critic learned termination probability.

    ``beta_omega(z_t) = sigma(w_beta^T * [z_t || e_omega])`` in ``(0, 1)``.

    Replaces stateful ``if streak >= 3: replan`` counters. When expected future
    value drops below the continuation baseline, ``beta`` rises smoothly and the
    option terminates. No hard counter, no 0/1 branch.

    Parameters
    ----------
    z:
        Current state representation.
    omega:
        Termination weight vector (includes bias as last element if desired).
    expert_embedding:
        Optional option/expert embedding ``e_omega`` concatenated to ``z``.
    """
    z = np.asarray(z, dtype=np.float64).ravel()
    if expert_embedding is not None:
        z = np.concatenate([z, np.asarray(expert_embedding, dtype=np.float64).ravel()])
    return float(sigmoid(omega @ z)[()])


class ALERSlotMemory:
    """ALER (Attention-Linked Entity Rewrite) slot memory.

    Replaces ``if obs_stale`` / ``if dialog_open`` state flags with a
    continuously-updated slot vector ``w_t in Delta^4``. Stale observations are
    down-weighted and overwritten by algebra rather than an if statement.

    Four slots (index 0..3): [current, pending, confirmed, stale].
    """

    def __init__(self, num_slots: int = 4, temperature: float = 1.0) -> None:
        self.num_slots = num_slots
        self.temperature = temperature
        # Slot weights, initialised uniform over active slots.
        self.w = np.full(num_slots, 1.0 / num_slots, dtype=np.float64)
        self.slots: List[Optional[Dict[str, Any]]] = [None] * num_slots

    def write(
        self,
        slot_index: int,
        value: Dict[str, Any],
        precision: float,
        write_gate: Optional[np.ndarray] = None,
    ) -> None:
        """Write ``value`` into ``slot_index`` with Gumbel-Softmax slot rewrite.

        ``precision`` is the continuous precision ``pi_{t,d}`` from the Free
        Energy objective. ``write_gate`` is an optional softmax over slots that
        determines how much each slot is overwritten.
        """
        self.slots[slot_index] = value
        if write_gate is None:
            # Default: concentrate write mass on the target slot.
            write_gate = np.zeros(self.num_slots, dtype=np.float64)
            write_gate[slot_index] = 1.0
        write_gate = np.asarray(write_gate, dtype=np.float64)
        # Precision scales how aggressively we overwrite.
        update = precision * write_gate
        self.w = softmax(np.log(np.clip(self.w, 1e-8, 1.0)) + update,
                         axis=-1, tau=self.temperature)

    def read(self, slot_index: int) -> Optional[Dict[str, Any]]:
        return self.slots[slot_index]

    def stale_mass(self) -> float:
        """Total attention mass on slots considered stale (slot 3 by default)."""
        return float(self.w[3]) if self.num_slots > 3 else 0.0

    def active_value(self) -> Optional[Dict[str, Any]]:
        """Value of the highest-weight non-stale slot."""
        order = np.argsort(-self.w[: max(1, self.num_slots - 1)])
        for idx in order:
            if self.slots[int(idx)] is not None:
                return self.slots[int(idx)]
        return None


# ---------------------------------------------------------------------------
# Entity Cross-Attention + Sink Token Router
# ---------------------------------------------------------------------------

class EntityRouter:
    """Cross-attention router over map entities with a learnable sink token.

    Replaces discrete ``if type_id == X: ... elif type_id == Y: ...`` entity
    lookup trees.  The current state is encoded as a query; available entities
    (NPCs, exits, monster groups, objectives) are encoded as keys/values.
    Cross-attention with a sink token produces a continuous distribution over
    actions: when no entity matches the query, mass collapses onto the sink
    token and the action tends toward ``state.snapshot`` / abstain.

    The router is used alongside the existing heuristic -- its output re-ranks
    or blends with the discrete branches rather than replacing them outright.
    """

    def __init__(self, dim: int = 16) -> None:
        self.dim = dim
        # Learnable projections (identity init; trainable later).
        self._W_q = np.eye(dim)
        self._W_k = np.eye(dim)
        self._W_v = np.eye(dim)
        # Sink token: represents "no entity matches / abstain".
        self._sink_key = np.zeros(dim)
        self._sink_key[0] = -1.0
        self._sink_value = np.zeros(dim)
        self._sink_value[0] = 1.0  # sink value signals "no-op / wait"

        # Action category embeddings (one per dimension slot).
        self._action_embeddings = {
            "fight.join": np.eye(dim)[0] if dim > 0 else np.ones(dim),
            "farm.start": np.eye(dim)[1] if dim > 1 else np.ones(dim),
            "quest.advance": np.eye(dim)[2] if dim > 2 else np.ones(dim),
            "nav.goto": np.eye(dim)[3] if dim > 3 else np.ones(dim),
            "state.snapshot": np.eye(dim)[4] if dim > 4 else np.ones(dim),
        }

    # -- entity encoding -----------------------------------------------------

    def _embed_entity(self, entity: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        """Encode an entity dict into (key, value) vectors."""
        key = np.zeros(self.dim, dtype=np.float64)
        val = np.zeros(self.dim, dtype=np.float64)
        text_parts = []
        for k, v in entity.items():
            if v is None:
                continue
            text_parts.append(f"{k}:{v}")
            # Hash each (key, value) pair into the key vector.
            h = abs(hash(f"{k}:{v}")) % self.dim
            key[h] += 1.0
            h2 = abs(hash(str(v))) % self.dim
            val[h2] += 1.0
        if text_parts:
            text_vec = self._embed_text(" ".join(text_parts))
            key = 0.7 * key + 0.3 * text_vec
            val = 0.7 * val + 0.3 * text_vec
        # Normalise.
        for vec in (key, val):
            n = np.linalg.norm(vec)
            if n > 0:
                vec /= n
        return key, val

    def _embed_text(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float64)
        for tok in _tokens(text) if text else []:
            h = abs(hash(tok)) % self.dim
            vec[h] += 1.0
        n = np.linalg.norm(vec)
        if n > 0:
            vec /= n
        return vec

    # -- routing -------------------------------------------------------------

    def route(
        self,
        state_query: np.ndarray,
        entities: Sequence[Dict[str, Any]],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Route a state query over entities via cross-attention + sink.

        Returns ``(action_logits, attention)`` where ``action_logits`` is a
        continuous score over the 5 action categories and ``attention`` shows
        how much mass went to each entity vs. the sink.
        """
        q = (self._W_q @ np.asarray(state_query, dtype=np.float64).ravel())

        if not entities:
            # No entities: all mass to sink -> abstain/snapshot.
            sink_attn = np.ones(1)
            return self._sink_action_logits(), sink_attn

        keys = []
        values = []
        for ent in entities:
            k, v = self._embed_entity(ent)
            keys.append(k)
            values.append(v)

        keys_arr = np.stack(keys)
        values_arr = np.stack(values)

        context, attn = cross_attend_with_sink(
            q[None, :], keys_arr, values_arr,
            self._sink_key, self._sink_value,
        )
        context = context[0]  # [dim]
        sink_mass = float(attn[0, 0])

        # Score each action category by similarity to the attended context.
        logits = {}
        for name, emb in self._action_embeddings.items():
            logits[name] = float(context @ emb)

        # The sink mass boosts state.snapshot (wait/no-op) and suppresses others.
        logits["state.snapshot"] += sink_mass
        for name in logits:
            if name != "state.snapshot":
                logits[name] *= (1.0 - sink_mass)

        names = list(logits.keys())
        action_logits = np.array([logits[n] for n in names], dtype=np.float64)
        return action_logits, attn[0]

    def _sink_action_logits(self) -> np.ndarray:
        """Action logits when no entities exist: favour state.snapshot."""
        names = list(self._action_embeddings.keys())
        logits = np.zeros(len(names), dtype=np.float64)
        snap_idx = names.index("state.snapshot")
        logits[snap_idx] = 1.0
        return logits

    def top_action(self, state_query: np.ndarray,
                   entities: Sequence[Dict[str, Any]]
                   ) -> Tuple[str, float, np.ndarray]:
        """Return (action_name, confidence, attention) for the top-scoring action."""
        logits, attn = self.route(state_query, entities)
        names = list(self._action_embeddings.keys())
        best_idx = int(np.argmax(logits))
        best_name = names[best_idx]
        # Confidence: softmax probability of the best action.
        probs = softmax(logits, tau=1.0)
        confidence = float(probs[best_idx])
        return best_name, confidence, attn


    def _embed_text(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float64)
        for tok in _route_tokens(text):
            h = abs(hash(tok)) % self.dim
            vec[h] += 1.0
        n = np.linalg.norm(vec)
        if n > 0:
            vec /= n
        return vec


# Local tokeniser (avoids circular import from agent.dialog_policy).
_WORD_RE = __import__("re").compile(r"[a-z0-9]+")


def _route_tokens(text: str) -> List[str]:
    if not isinstance(text, str):
        return []
    import unicodedata as _ud
    plain = _ud.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _WORD_RE.findall(plain.casefold())
