# 8-Instance Headless Soak & Memory/Latency Profile Report

- **Verdict:** **PASSED**
- **Configured Slots:** 8 instances
- **Duration:** 305.55s (60 samples)

## 1. Budget & SLO Verification

| Component / Metric | Shipped Budget | Measured Peak | Verdict |
|:-------------------|:---------------|:--------------|:--------|
| **C# Slot Private WS** | `< 200.0 MB` | `24.87 MB` | PASS |
| **Combat Solve Latency ($p_{99}$)** | `< 10.0 ms` | `3.01 ms` | PASS |
| **UiDispatcher.Dropped** | `0` | `0` | PASS |

## 2. Monotonic Leak Slope Analysis

Linear regression fits $y = m \cdot t + c$ over the soak time series:

| Metric | Slope ($m$/s) | Rate ($m$/min) | $R^2$ Fit | Delta | Leak Detected |
|:-------|:--------------|:---------------|:----------|:------|:--------------|
| **C# Total Private WS (MB)** | `+0.020414` | `+1.2249` | `0.2042` | `+24.96` | NO (FLAT) |
| **C# Total Handles** | `-0.279928` | `-16.7957` | `0.7744` | `-88.00` | NO (FLAT) |
| **Go Relay Heap Inuse (MB)** | `+0.019923` | `+1.1954` | `0.1134` | `+18.83` | NO (FLAT) |
| **Go Relay Goroutines** | `+0.000195` | `+0.0117` | `0.0009` | `+0.00` | NO (FLAT) |
| **Combat Server RAM (MB)** | `-0.000077` | `-0.0046` | `0.0224` | `+0.00` | NO (FLAT) |
| **Storage WAL Total (MB)** | `+0.000079` | `+0.0048` | `0.0036` | `+0.17` | NO (FLAT) |

## 3. Per-Slot Private Working Set Breakdown

| Slot Name | Peak Private WS (MB) | Budget (200 MB) | Margin |
|:----------|:---------------------|:----------------|:-------|
| `dofus-01` | `24.85 MB` | `200.00 MB` | `+175.15 MB` |
| `dofus-02` | `24.82 MB` | `200.00 MB` | `+175.18 MB` |
| `dofus-03` | `24.82 MB` | `200.00 MB` | `+175.18 MB` |
| `dofus-04` | `24.84 MB` | `200.00 MB` | `+175.16 MB` |
| `dofus-05` | `24.84 MB` | `200.00 MB` | `+175.16 MB` |
| `dofus-06` | `24.79 MB` | `200.00 MB` | `+175.21 MB` |
| `dofus-07` | `24.79 MB` | `200.00 MB` | `+175.21 MB` |
| `dofus-08` | `24.87 MB` | `200.00 MB` | `+175.13 MB` |

## 4. Combat Server Decision Latencies

- **Sampled Turns:** 945
- **Min Latency:** `1.0 ms`
- **p50 Latency:** `1.5 ms`
- **p95 Latency:** `2.2 ms`
- **p99 Latency:** `2.7 ms`
- **Max Latency:** `9.0 ms`

## 5. Python tracemalloc Top Allocators

| Filename | Line | Total Bytes | Allocation Count |
|:---------|:-----|:------------|:-----------------|
| `<frozen importlib._bootstrap_external>` | `511` | `280,436 B` | `2,083` |
| `__init__.py` | `771` | `132,204 B` | `15` |
| `source_info_util.py` | `213` | `78,432 B` | `410` |
| `flattree.py` | `234` | `73,880 B` | `856` |
| `partial_eval.py` | `1841` | `70,040 B` | `169` |
| `<frozen importlib._bootstrap>` | `491` | `58,556 B` | `573` |
| `battle_solver.py` | `359` | `54,336 B` | `48` |
| `pjit.py` | `647` | `47,760 B` | `127` |
| `sharding_impls.py` | `191` | `47,400 B` | `601` |
| `pjit.py` | `556` | `46,848 B` | `125` |
