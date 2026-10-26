# Golf campaign brief (read this first)

You own exactly ONE file. Optimize it, write the result to `golf/<same relative path>`,
and never touch the original or any other file. Do not edit `golf/done.md` or
`golf/EXPLANATION.md`; the orchestrator owns those.

## North star

Same observable behavior. Fewer bytes. Fewer allocations. Faster hot path.
This is a solo, write-only codebase: terse and clever is allowed. Broken is not.

## Hard invariants

- Keep every public module/class/function/method name, signature, default value,
  exported constant, CLI flag, env var, file format, JSON key, and sort order.
- Keep every string that another module or log parser may match: exception messages,
  tool names, verdict strings (`advanced`/`noop`/`failed`/`blocked`), protocol literals.
- Keep side-effect order: I/O, logging, RNG calls, time calls, mutation of arguments.
- Keep numeric semantics: int vs float, rounding, overflow, NaN, division by zero.
- No new third-party dependencies. Python is 3.14, Go 1.27, .NET per desktop project.
- Do not add network access, threads, asyncio, caching with observable staleness, or
  global mutable state that changes results between calls.

## Allowed (use aggressively)

- Delete comments and docstrings, except shebang/encoding/license and behavior-bearing
  pragmas (`# noqa`, `# type: ignore`, `# pragma`). Keep dataclass/Pydantic annotations
  that are required at runtime.
- Delete type annotations that have no runtime effect, if the file does not rely on
  `typing.get_type_hints` / Pydantic / dataclasses. When unsure, keep them.
- Collapse one-call wrappers, single-use temporaries, and trivial branches.
- Comprehensions, generator-free tight loops, local binding of module globals and methods.
- `__slots__` for hot classes with fixed attributes (only when no dynamic attrs are used).
- Precomputed tuples/dicts/sets at module scope; hoist loop invariants.
- `dict.setdefault`, `zip`, `itertools`, `functools.lru_cache` only for provably pure calls.
- Early exits, branchless arithmetic, bit tricks, `int`/`float` shortcuts.
- For C#: `Span<T>`, `stackalloc`, `ref` locals, `CollectionsMarshal`, `Unsafe`/`MemoryMarshal`
  when the project already permits unsafe; keep public surface identical.
- For Go: preallocated slices, `sync.Pool` only if ownership is clear, `strings.Builder`,
  byte-level parsing, avoiding interface boxing; keep behavior and tests green.
- For JS: monomorphic loops, typed arrays, bitwise 32-bit math, no intermediate arrays.

## Forbidden

- Deleting validation, error handling, or bounds checks that prevent real crashes.
- Changing exception types, return shapes, or `None` vs missing-key behavior.
- Reordering independent side effects.
- Replacing a stable algorithm with an approximate one.
- "Optimizing" generated bundles, vendored code, or files outside your assignment.
- Adding test-only branches or environment sniffing.

## Verify before reporting

Python (no bytecode written):
```
python -c "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())" golf/<path>
```
Then, when the module is importable and side-effect free, load original and golfed copies
via `importlib.util.spec_from_file_location` and fuzz pure helpers on random inputs.
Use `PYTHONPATH=dofus-network` and `PYTHONDONTWRITEBYTECODE=1` when importing the package.
If import is unsafe (CLI side effects, network), say so and rely on AST + manual review.

Go: `gofmt -w` the output, then `go test ./...` if the file is in `scripts/` and tests exist.
C#: preserve namespaces/visibility; if `dotnet` is unavailable, report that.
JS: `node --check golf/<path>`.

## Report (<= 15 lines)

```
FILE: <relative path>
OUT: golf/<relative path>
BYTES: <before> -> <after> (-<pct>%)
LINES: <before> -> <after>
VERIFY: <ast|node|gofmt|go test|fuzz N cases|not run + why>
TRICKS: <3-6 comma-separated>
RISK: <low|medium|high> <one clause>
NOTES: <only if a public behavior was intentionally kept verbatim>
```
