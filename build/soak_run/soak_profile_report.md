# 8-Instance Headless Soak & Memory/Latency Profile Report

- **Verdict:** **PASSED**
- **Configured Slots:** 8 instances
- **Duration:** 16.32s (10 samples)

## 1. Budget & SLO Verification

| Component / Metric | Shipped Budget | Measured Peak | Verdict |
|:-------------------|:---------------|:--------------|:--------|
| **C# Slot Private WS** | `< 200.0 MB` | `22.92 MB` | PASS |
| **Combat Solve Latency ($p_{99}$)** | `< 10.0 ms` | `6.46 ms` | PASS |
| **UiDispatcher.Dropped** | `0` | `0` | PASS |

## 2. Monotonic Leak Slope Analysis

Linear regression fits $y = m \cdot t + c$ over the soak time series:

| Metric | Slope ($m$/s) | Rate ($m$/min) | $R^2$ Fit | Delta | Leak Detected |
|:-------|:--------------|:---------------|:----------|:------|:--------------|
| **C# Total Private WS (MB)** | `+0.584970` | `+35.0982` | `0.4383` | `+9.47` | NO (FLAT) |
| **C# Total Handles** | `-0.667548` | `-40.0529` | `0.4833` | `-8.00` | NO (FLAT) |
| **Go Relay Heap Inuse (MB)** | `+0.203323` | `+12.1994` | `0.9607` | `+2.47` | NO (FLAT) |
| **Go Relay Goroutines** | `+0.024858` | `+1.4915` | `0.0763` | `+0.00` | NO (FLAT) |
| **Combat Server RAM (MB)** | `+0.003249` | `+0.1949` | `0.7283` | `+0.05` | NO (FLAT) |
| **Storage WAL Total (MB)** | `+0.148311` | `+8.8986` | `0.9942` | `+1.59` | NO (FLAT) |

## 3. Per-Slot Private Working Set Breakdown

| Slot Name | Peak Private WS (MB) | Budget (200 MB) | Margin |
|:----------|:---------------------|:----------------|:-------|
| `dofus-01` | `22.92 MB` | `200.00 MB` | `+177.08 MB` |
| `dofus-02` | `22.83 MB` | `200.00 MB` | `+177.17 MB` |
| `dofus-03` | `22.79 MB` | `200.00 MB` | `+177.21 MB` |
| `dofus-04` | `22.89 MB` | `200.00 MB` | `+177.11 MB` |
| `dofus-05` | `22.84 MB` | `200.00 MB` | `+177.16 MB` |
| `dofus-06` | `22.80 MB` | `200.00 MB` | `+177.20 MB` |
| `dofus-07` | `22.77 MB` | `200.00 MB` | `+177.23 MB` |
| `dofus-08` | `22.85 MB` | `200.00 MB` | `+177.15 MB` |

## 4. Combat Server Decision Latencies

- **Sampled Turns:** 35
- **Min Latency:** `1.5 ms`
- **p50 Latency:** `3.0 ms`
- **p95 Latency:** `4.78 ms`
- **p99 Latency:** `6.256 ms`
- **Max Latency:** `6.8 ms`

## 5. Python tracemalloc Top Allocators

| Filename | Line | Total Bytes | Allocation Count |
|:---------|:-----|:------------|:-----------------|
| `<frozen importlib._bootstrap_external>` | `511` | `280,420 B` | `2,084` |
| `__init__.py` | `771` | `132,086 B` | `13` |
| `source_info_util.py` | `213` | `78,432 B` | `410` |
| `flattree.py` | `234` | `73,880 B` | `856` |
| `partial_eval.py` | `1841` | `70,040 B` | `169` |
| `<frozen importlib._bootstrap>` | `491` | `58,646 B` | `575` |
| `battle_solver.py` | `359` | `54,336 B` | `48` |
| `pjit.py` | `647` | `47,760 B` | `127` |
| `sharding_impls.py` | `191` | `47,400 B` | `601` |
| `pjit.py` | `556` | `46,848 B` | `125` |
