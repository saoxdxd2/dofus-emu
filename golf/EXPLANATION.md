# EXPLANATION.md — what the golfed tree actually does

This is the companion to `golf/done.md`. It covers the committed wave-1 artifacts and the
two files being rebuilt. Everything here is staged: the originals still run the app.

## Global mental model

A golf pass is not "minify then hope". Each file is treated as a behavior contract with a
hot core:

1. **Lock the contract.** Public names, signatures, JSON keys, tool names, verdict strings,
   exception text, and side-effect order stay byte-identical. That is what keeps the golfed
   copy drop-in.
2. **Delete only what the interpreter never reads.** Comments, docstrings, redundant type
   annotations, dead aliases, and single-use temporaries.
3. **Move work out of loops.** Precompute tables and regexes at import time, bind methods
   and module globals to locals, replace repeated attribute walks with tuple unpacking.
4. **Change the shape of data, not the answer.** Tuples instead of lists for immutable
   tables, `__slots__` instead of `__dict__`, generators only where the caller consumes once.
5. **Prove it.** AST/parse gate always; differential fuzz on pure helpers wherever the
   module can be imported without side effects; `go test` where the repo already has an
   equivalence suite.

The house style is deliberately write-only: tab indentation, one-line branches, dense
tuple constants. Python accepts it; humans read `done.md` instead.

## Arcane mechanics used across the tree

- **AST/byte minification.** Some files were parsed and re-emitted without docstrings, then
  hand-tightened. This preserves semantics while removing 30–60% of the bytes in
  documentation-heavy modules (`autopilot.py` is the extreme case: ~2.2k lines, most of it
  prose).
- **Import-time constant folding.** `frozenset`, tuple tables, compiled regexes, and
  `lru_cache`-wrapped pure pricing functions replace per-call construction.
- **`__slots__` on hot value objects.** Removes the per-instance `__dict__` pointer and
  turns attribute access into a fixed offset. Only applied where no dynamic attributes are
  used; dataclass `slots=True` where the class is a dataclass.
- **Local aliasing as a performance feature.** `_CALC = DofusDamageCalculator.calculate_spell_damage`
  is not noise; it removes a module + class attribute walk from the hot path. The campaign
  explicitly rejects byte wins that delete these (see the rejected `spells` pass).
- **Branchless / early-exit rewrites.** `if not x: return` ordering, `dict.get` defaults,
  set membership instead of list scans, and tuple unpacking instead of indexed reads.
- **Differential fuzzing.** Original and golfed modules are loaded side-by-side with
  `importlib.util.spec_from_file_location`; random inputs are pushed through both and the
  outputs are compared. This is how the pure helpers earned "low risk" instead of "looks
  fine".

## Per-file notes

### `dofus-network/combat/battle_solver.py`

**Summary.** The combat search core: map cells, neighbours, line-of-sight, spell range
rings, reachability BFS, and damage/effect evaluation. The golfed version keeps the same
search order and the same result tuples; it removes the per-call construction of geometry
tables and the temporary lists inside the inner loops.

**Arcane mechanics.** `_POINTS` inverts `cell_point(cell)` once at import, so the hot path
does a dict lookup instead of recomputing isometric coordinates. Neighbour tables and range
rings are cached by geometry (`(limit, origin, min_range, max_range)`), because the solver
asks for the same ring on many candidate cells. Spell flags (`has_teleport`, damage/heal
effect partitions) are cached on the `LiveSpell` object. Board helpers return tuples, not
lists, so they can be shared without defensive copies. The final pass used an AST round-trip
to drop comments/docstrings, then hand-edited the remaining hot loops.

**Performance edge.** The dominant cost was allocation, not arithmetic: every candidate cell
rebuilt neighbour/range lists and re-filtered effects. Precomputation moves that to import
time and turns the inner loop into tuple iteration over contiguous references. The BFS keeps
the original FIFO order (a heap was already rejected upstream as pure overhead), so cache
behavior and result ordering are unchanged.

Gate: AST + import/`self_check` + three fuzz suites (helpers, random boards, edge cases),
0 failures.

### `dofus-network/agent/executor.py`

**Summary.** The tool executor: validates a tool call, coerces arguments, dispatches to the
client bridge, and normalizes the response. The golfed version compresses dispatch and
validation while preserving tool names, coercion rules, error strings, and return shapes.

**Arcane mechanics.** Dispatch is driven by the existing table rather than a chain of
`if tool == ...`; payload validation reuses hoisted field sets; repeated `os.environ` and
module-attribute lookups are bound once per call. The module already uses `OrderedDict` and
`deque`; those were kept because their ordering semantics are part of the contract. Unicode
normalization is kept at the same point in the pipeline.

**Performance edge.** The win is per-call overhead: fewer temporary dict/list constructions,
fewer attribute walks, and earlier rejection of malformed payloads before any bridge work.
This is the path every agent step crosses, so constant-factor cuts multiply by step count.

Gate: AST. The agent's fuzz report was lost in the rollback, so a differential re-run is
queued before this row is upgraded from "recovered" to "fully proven".

### `dofus-network/networkhealth/sentinel.py`

**Summary.** Network-health checks over the relay/session: parses endpoint data, evaluates
messages, and emits a health report. The golfed version is 36% smaller and measurably faster
on the two hottest helpers.

**Arcane mechanics.** Regexes are compiled once at module scope; dataclass field-name sets
are frozen at import instead of rebuilt per call; per-call lists became tuple constants;
walrus expressions collapse "look up then test" spell/field probes. Bound methods
(`_send_json`, repository and layout handles) are hoisted into locals inside loops.

**Performance edge.** Agent benchmarks on the golfed copy: `evaluate_message` ~11% faster,
`_safe_char_stats` ~41% faster. The gain comes from removing repeated regex compilation and
set construction, plus fewer local/global lookups in the report loop, not from changing the
checks themselves.

Gate: AST + 9,000 differential fuzz cases + 78 endpoint diffs; 0 mismatches.

### `scripts/dofus-net-proxy.go`

**Summary.** The Go relay: HTTP/CONNECT proxying, asset cache, CDN pool, egress pinning,
header rewriting, and the UA/client-disguise helpers. This is the highest-value non-Python
artifact because it sits on every request.

**Arcane mechanics.** The big win is replacing regexp matching with byte-level scanners for
the UA token stripper, cache-asset detector, and Accept-Encoding filter. Each scanner is
differentially fuzzed against the old regex implementation; the repo's own
`dofus_net_proxy_equiv_test.go` remains the contract. Header maps are reused where ownership
is clear, response builders preallocate with `strings.Builder`, and canonical header keys /
hop-by-hop sets are shared instead of rebuilt. Private identifiers were renamed by AST, but
every identifier referenced by the equivalence tests was preserved.

**Performance edge.** Per-request allocations dominate a proxy: header maps, concatenated
strings, and `regexp` scratch. The golfed path removes the regex engine from the common
case, pools buffers, and reuses header slices. The `go test` suite (including the 20k-case
UA fuzz and edge tests) is green in 0.09 s; the agent also ran ~5.8M scratch differential
cases and `go vet` clean.

Gate: `gofmt` + `go test ./...` green.

### `dofus-network/advisor/spells.py`

**Summary.** Spell-data advisor: parses spell power/effects and builds rotation/utility
plans. The committed artifact is the prior golf pass; this campaign audited it and rejected
a replacement that saved 14 bytes by deleting a hot-path alias.

**Arcane mechanics.** Precompiled regexes, `lru_cache` on pure utility pricing, single-pass
effect parsing, reused `TargetStats`, mutated `SpellInfo` instead of rebuilding it, and
precomputed rotation keys/AP. `__slots__` on hot value dataclasses. Dict key order and
warning/label strings are preserved because downstream JSON comparisons depend on them.

**Performance edge.** Parsing is the cost center; compiling regexes once and avoiding a
second pass over effects cuts the per-spell constant. `_CALC` keeps the damage calculator
call to one attribute walk. The rejected pass is a useful example of the campaign's
tie-break: 64 bytes is not worth a per-spell lookup.

Gate: AST + prior fuzz evidence; replacement rejected.

### `dofus-network/ai/encoder_policy.py`

**Summary.** Multi-task encoder policy: one forward pass produces tool logits plus argument
heads (presence/bool/span/enum). The committed artifact is the prior golf pass. The agent
rewrite was rejected because it made the forward pass slower.

**Arcane mechanics.** The retained version reuses the token embedding (`emb`) for the
lexical head and computes `pooled @ field_weights.t()` once for both presence and bool
heads. The rejected rewrite called `tok_emb` a second time and duplicated the projection
matmul, buying 80 bytes with extra FLOPs — exactly the failure mode the tie-break clause now
forbids. Weight-tied token embedding and the anchor-scale path are untouched.

**Performance edge.** One embedding lookup and one projection instead of two each, on every
forward pass. In a training/inference loop that is strictly better than a byte-level win.
The file remains AST-gated; a tensor-equivalence run needs the model stack and is queued with
the next PyTorch-capable pass.

Gate: AST; replacement rejected.

### `dofus-network/agent/planner.py` (rebuilding)

**Summary.** Turns the quest graph into one tool call: goal classification, quest choice,
objective translation, farm targeting, and dialog handling. The source gained 39 lines of
dialog-exhaustion logic during the campaign (`DIALOG_ASK_LIMIT`, `_dialog_asks`,
`_dialog_exhausted`, 256-entry pruning, fork copying), so the first golfed copy was stale and
is being rebuilt against the current bytes.

**Arcane mechanics.** Precompiled farm/goal regexes, set-based candidate selection, early
exits for invisible/unreachable targets, hoisted invariant payload fields, and the dialog
memo keyed by `(npc, page, reply, objective)`. The memo is capped at 256 entries and prunes
the oldest 32; forks copy both the ask counts and the exhausted set so a twin does not re-ask
a dead page.

**Performance edge.** The planner runs once per agent step, not per packet, so the win is
mostly allocation and repeated JSON/dict walking. The dialog memo also removes a pathological
loop (the baseline re-asked the same reply 87 times against one objective), which is a much
larger real-world win than the byte count.

Gate: pending AST + differential fuzz on current source.

### `dofus-network/combat/fight_estimate.py` (rebuilding)

**Summary.** Estimates fight outcome from stats, resistances, AP/turn costs, and spell
damage. The golfed pass keeps numeric results bit-exact, including float rounding and clamp
order.

**Arcane mechanics.** `dataclass(slots=True)` on hot value objects; inlined resistance-factor
and AP/turn-cost loops; hoisted `dpt.items()` and cached totals; a calibrator fit that
precomputes standardized columns and transposed gradients with explicit `+=` accumulation.
That last detail matters: switching to `sum()`/compensated summation would change the
floating-point result, so the golfed version deliberately keeps the original accumulation
order.

**Performance edge.** The estimator evaluates many candidate actions; removing per-candidate
dict walks and dataclass `__dict__` traffic is the main win. Bit-exactness is the constraint
that rules out most "clever" algebraic rewrites.

Gate: pending AST + differential fuzz against the original (helpers, calibrator, report,
CLI paths).
