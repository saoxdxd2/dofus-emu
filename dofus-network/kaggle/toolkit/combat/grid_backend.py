"""Array backend for the 30M 560-cell combat grid model: jax when available, numpy otherwise.

Mirrors ``combat/encounter/backend.py`` so the same ``models/combat_grid/model.pkl``
checkpoint (108 MB of JAX-trained weights) loads and runs identically on the
training box (jax, jit-compiled) and on the installed embedded interpreter
(numpy, eager). PyPI publishes no cp311 jaxlib wheel, so the shipped interpreter
cannot import jax at all; without this backend the model is silently dead weight
and every fight falls back to the deterministic solver.

Everything the forward pass needs is dense linear algebra -- matmul, layernorm,
relu, masked softmax, einsum, argmax, where, stack, sum, concatenate -- all of
which numpy supplies. A numpy forward is the same computation as jax eager,
just uncompiled, so bit-for-bit identical results on a single board.

``DOFUS_GRID_BACKEND=numpy`` forces the fallback even where jax is installed,
which makes the parity claim testable: same checkpoint, same boards, both
backends, compared by ``combat/plan_benchmark.py``.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Tuple

FORCE_NUMPY = os.environ.get("DOFUS_GRID_BACKEND", "").strip().lower() == "numpy"

try:
    if FORCE_NUMPY:
        raise ImportError("DOFUS_GRID_BACKEND=numpy")
    import jax
    import jax.numpy as jnp
    from jax import lax as _lax

    HAS_JAX = True
    BACKEND = "jax"
    XP = jnp
    f32 = jnp.float32
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

    def tree_leaves(tree: Any) -> List[Any]:
        return jax.tree_util.tree_leaves(tree)

    class _IndexAt:
        """jax's functional indexed update, `x.at[idx].set(v)` / `.add(v)`."""

        def __init__(self, array: Any) -> None:
            self._array = array

        def __getitem__(self, index: Any) -> "_Indexed":
            return _Indexed(self._array, index)

    class _Indexed:
        __slots__ = ("_array", "_index")

        def __init__(self, array: Any, index: Any) -> None:
            self._array = array
            self._index = index

        def set(self, value: Any) -> Any:
            return self._array.at[self._index].set(value)

        def add(self, value: Any) -> Any:
            return self._array.at[self._index].add(value)

    def at(array: Any) -> _IndexAt:
        return _IndexAt(array)

    class _Random:
        """jax.random under a name the model can call without knowing the backend."""

        @staticmethod
        def split(key: Any, count: int) -> Any:
            return list(jax.random.split(key, count))

        @staticmethod
        def normal(key: Any, shape: Tuple[int, ...]) -> Any:
            return jax.random.normal(key, shape)

    random = _Random()

except ImportError as _import_error:  # the installed copy lands here
    import numpy as XP

    jax = None  # type: ignore[assignment]
    HAS_JAX = False
    BACKEND = "numpy"
    # A machine that *has* jax but fails to load it is a broken environment; an
    # inference-only install with no jax is by design and is reported this way.
    JAX_IMPORT_ERROR = "%s: %s" % (type(_import_error).__name__, _import_error)
    f32 = XP.float32

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
        return fn  # no tracing, no compiled path on numpy

    def tree_map(fn: Callable, tree: Any) -> Any:
        if isinstance(tree, dict):
            return {key: tree_map(fn, value) for key, value in tree.items()}
        if isinstance(tree, (list, tuple)):
            return type(tree)(tree_map(fn, value) for value in tree)
        return fn(tree)

    def tree_leaves(tree: Any) -> List[Any]:
        if isinstance(tree, dict):
            leaves: List[Any] = []
            for value in tree.values():
                leaves.extend(tree_leaves(value))
            return leaves
        if isinstance(tree, (list, tuple)):
            leaves = []
            for value in tree:
                leaves.extend(tree_leaves(value))
            return leaves
        return [tree]

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
            # Unbuffered, exactly like jax's `.add`: several rows landing on the
            # same cell accumulate instead of one silently winning.
            XP.add.at(out, self._index, value)
            return out

    class _IndexAt:
        def __init__(self, array: Any) -> None:
            self._array = array

        def __getitem__(self, index: Any) -> _Indexed:
            return _Indexed(self._array, index)

    def at(array: Any) -> _IndexAt:
        """The same functional contract as `x.at[idx]`: the array is never mutated."""
        return _IndexAt(array)

    class _Random:
        """A deterministic stand-in so the module stays importable without jax.

        Training never runs here -- it needs jax's gradients -- but a module that
        cannot be imported is worse than a function that refuses, so the shapes
        exist and the calls refuse loudly instead of at import time.
        """

        @staticmethod
        def split(key: Any, count: int) -> Any:
            raise RuntimeError(
                "the random stream needs the jax backend; the numpy fallback is "
                "for inference only (train on a machine with jax)"
            )

        @staticmethod
        def normal(key: Any, shape: Tuple[int, ...]) -> Any:
            raise RuntimeError(
                "the random stream needs the jax backend; the numpy fallback is "
                "for inference only (train on a machine with jax)"
            )

    random = _Random()

__all__ = [
    "XP", "BACKEND", "HAS_JAX", "FORCE_NUMPY", "f32",
    "relu", "softmax", "log_softmax", "rsqrt", "jit",
    "tree_map", "tree_leaves", "random", "at", "JAX_IMPORT_ERROR",
]
