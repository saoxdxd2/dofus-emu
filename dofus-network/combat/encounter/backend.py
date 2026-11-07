"""The array backend the learned fight models run on: jax when it is there, numpy when it is not.

Why this exists: `models/encounter_selfplay.pkl` -- the trained fight policy -- ships in the
installer, and until now an installed copy could not *use* it. `combat/encounter/model.py`,
`combat/plan_value.py` and `combat/live_policy.py` imported jax at module level, and the interpreter
the payload ships (3.11.9) has no jax: PyPI publishes no cp311 jaxlib wheel at all, and the newest
one that does costs 59.9 MB and is eighteen months old (ARCH_REASSESSMENT.md §L.5). So every
installed fight fell back to the deterministic solver and `/health` said
`backend=solver-only (jax absent)` -- a decided feature that was not in the product.

Everything these modules need from an array library is dense linear algebra -- matmul, layernorm,
relu, masked softmax, `einsum`, `argmax`, `where`, `stack` -- and numpy has all of it. jax stays the
preferred backend on the training box (its `jit` compiles the batch value function); numpy is the
fallback, not a second implementation of the model, so the two cannot drift.

`DOFUS_FIGHT_BACKEND=numpy` forces the fallback even where jax is installed. That is what makes the
parity claim testable on one machine: the same checkpoint, the same boards, both backends, in one
process per run, compared by `combat/plan_benchmark.py`.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, Tuple

FORCE_NUMPY = os.environ.get("DOFUS_FIGHT_BACKEND", "").strip().lower() == "numpy"

try:
    if FORCE_NUMPY:
        raise ImportError("DOFUS_FIGHT_BACKEND=numpy")
    import jax
    import jax.numpy as XP
    from jax import lax as _lax

    HAS_JAX = True
    BACKEND = "jax"
    JAX_IMPORT_ERROR = None

    def relu(x: Any) -> Any:
        return jax.nn.relu(x)

    def softmax(x: Any, axis: int = -1) -> Any:
        return jax.nn.softmax(x, axis=axis)

    def log_softmax(x: Any, axis: int = -1) -> Any:
        return jax.nn.log_softmax(x, axis=axis)

    def rsqrt(x: Any) -> Any:
        return _lax.rsqrt(x)

    def jit(fn: Callable, **kwargs: Any) -> Callable:
        return jax.jit(fn, **kwargs)

    def tree_map(fn: Callable, tree: Any) -> Any:
        return jax.tree_util.tree_map(fn, tree)

    def at(array: Any) -> Any:
        """jax's functional indexed update, `x.at[idx].set(v)` / `.add(v)`."""
        return array.at

    class _Random:
        """jax.random under a name the model can call without knowing the backend."""

        @staticmethod
        def split(key: Any, count: int) -> Any:
            return list(jax.random.split(key, count))

        @staticmethod
        def normal(key: Any, shape: Tuple[int, ...]) -> Any:
            return jax.random.normal(key, shape)

    random = _Random()
except ImportError as _import_error:               # the installed copy lands here
    import numpy as XP

    jax = None                                         # type: ignore[assignment]
    HAS_JAX = False
    BACKEND = "numpy"
    # A machine that *has* jax but fails to load it must not be reported the same way as
    # an inference-only install: the first is a broken environment, the second is by design.
    JAX_IMPORT_ERROR = "%s: %s" % (type(_import_error).__name__, _import_error)

    def relu(x: Any) -> Any:
        return XP.maximum(x, 0.0)

    def softmax(x: Any, axis: int = -1) -> Any:
        shifted = x - XP.max(x, axis=axis, keepdims=True)
        exp = XP.exp(shifted)
        return exp / XP.sum(exp, axis=axis, keepdims=True)

    def log_softmax(x: Any, axis: int = -1) -> Any:
        shifted = x - XP.max(x, axis=axis, keepdims=True)
        return shifted - XP.log(XP.sum(XP.exp(shifted), axis=axis, keepdims=True))

    def rsqrt(x: Any) -> Any:
        return 1.0 / XP.sqrt(x)

    def jit(fn: Callable, **kwargs: Any) -> Callable:
        return fn                                       # no tracing, no compiled path

    def tree_map(fn: Callable, tree: Any) -> Any:
        if isinstance(tree, dict):
            return {key: tree_map(fn, value) for key, value in tree.items()}
        if isinstance(tree, (list, tuple)):
            return type(tree)(tree_map(fn, value) for value in tree)
        return fn(tree)

    class _Indexed:
        __slots__ = ("_array", "_index")

        def __init__(self, array: Any, index: Any) -> None:
            self._array = array
            self._index = index

        def set(self, value: Any) -> Any:
            out = self._array.copy()
            out[self._index] = value
            return out

        def add(self, value: Any) -> Any:
            out = self._array.copy()
            # Unbuffered, exactly like jax's `.add`: several rows landing on the same cell
            # accumulate instead of one silently winning. `out[idx] += v` would do the latter.
            XP.add.at(out, self._index, value)
            return out

    class _IndexAt:
        __slots__ = ("_array",)

        def __init__(self, array: Any) -> None:
            self._array = array

        def __getitem__(self, index: Any) -> _Indexed:
            return _Indexed(self._array, index)

    def at(array: Any) -> _IndexAt:
        """The same functional contract as `x.at[idx]`: the array is never mutated."""
        return _IndexAt(array)

    class _Random:
        """A deterministic stand-in so `init_params` stays importable without jax.

        Training never runs here -- it needs jax's gradients -- but a module that cannot be
        imported is worse than a function that refuses, so the shapes exist and the calls refuse
        loudly instead of at import time.
        """

        @staticmethod
        def split(key: Any, count: int) -> Any:
            raise RuntimeError("the random stream needs the jax backend; the numpy fallback is "
                               "for inference only (train on a machine with jax)")

        @staticmethod
        def normal(key: Any, shape: Tuple[int, ...]) -> Any:
            raise RuntimeError("the random stream needs the jax backend; the numpy fallback is "
                               "for inference only (train on a machine with jax)")

    random = _Random()


# The model's arrays are float32 end to end. numpy would happily promote a python float to float64
# in `x * 0.5`, which silently doubles the memory of every activation and still returns an answer
# the jax path would never produce.
Array = Any
f32 = XP.float32

__all__ = ["XP", "BACKEND", "HAS_JAX", "FORCE_NUMPY", "Array", "f32",
           "relu", "softmax", "log_softmax", "rsqrt", "jit", "tree_map", "random", "at"]
