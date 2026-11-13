# 8-Instance Headless Soak & Memory/Latency Profile Report

- **Verdict:** **PASSED**
- **Configured Slots:** 8 instances
- **Duration:** 65.66s (56 samples)

## 1. Budget & SLO Verification

| Component / Metric | Shipped Budget | Measured Peak | Verdict |
|:-------------------|:---------------|:--------------|:--------|
| **C# Slot Private WS** | `< 200.0 MB` | `23.93 MB` | PASS |
| **Combat Solve Latency ($p_{99}$)** | `< 10.0 ms` | `3.64 ms` | PASS |
| **UiDispatcher.Dropped** | `0` | `0` | PASS |

## 2. Monotonic Leak Slope Analysis

Linear regression fits $y = m \cdot t + c$ over the soak time series:

| Metric | Slope ($m$/s) | Rate ($m$/min) | $R^2$ Fit | Delta | Leak Detected |
|:-------|:--------------|:---------------|:----------|:------|:--------------|
| **C# Total Private WS (MB)** | `+0.193850` | `+11.6310` | `0.8308` | `+17.95` | NO (FLAT) |
| **C# Total Handles** | `-0.526288` | `-31.5773` | `0.3897` | `-40.00` | NO (FLAT) |
| **Go Relay Heap Inuse (MB)** | `+0.217426` | `+13.0456` | `0.9987` | `+13.84` | NO (FLAT) |
| **Go Relay Goroutines** | `+0.008558` | `+0.5135` | `0.0434` | `+1.00` | NO (FLAT) |
| **Combat Server RAM (MB)** | `+0.001794` | `+0.1076` | `0.3687` | `+0.03` | NO (FLAT) |
| **Storage WAL Total (MB)** | `+0.115585` | `+6.9351` | `0.9987` | `+7.28` | NO (FLAT) |

## 3. Per-Slot Private Working Set Breakdown

| Slot Name | Peak Private WS (MB) | Budget (200 MB) | Margin |
|:----------|:---------------------|:----------------|:-------|
| `dofus-01` | `23.91 MB` | `200.00 MB` | `+176.09 MB` |
| `dofus-02` | `23.83 MB` | `200.00 MB` | `+176.17 MB` |
| `dofus-03` | `23.91 MB` | `200.00 MB` | `+176.09 MB` |
| `dofus-04` | `23.92 MB` | `200.00 MB` | `+176.08 MB` |
| `dofus-05` | `23.93 MB` | `200.00 MB` | `+176.07 MB` |
| `dofus-06` | `23.86 MB` | `200.00 MB` | `+176.14 MB` |
| `dofus-07` | `23.86 MB` | `200.00 MB` | `+176.14 MB` |
| `dofus-08` | `23.88 MB` | `200.00 MB` | `+176.12 MB` |

## 4. Combat Server Decision Latencies

- **Sampled Turns:** 194
- **Min Latency:** `1.2 ms`
- **p50 Latency:** `1.6 ms`
- **p95 Latency:** `2.6 ms`
- **p99 Latency:** `3.107 ms`
- **Max Latency:** `4.0 ms`

## 5. Python tracemalloc Top Allocators

| Filename | Line | Total Bytes | Allocation Count |
|:---------|:-----|:------------|:-----------------|
| `<frozen importlib._bootstrap_external>` | `511` | `280,436 B` | `2,083` |
| `__init__.py` | `771` | `132,204 B` | `15` |
| `source_info_util.py` | `213` | `78,432 B` | `410` |
| `flattree.py` | `234` | `73,880 B` | `856` |
| `partial_eval.py` | `1841` | `70,040 B` | `169` |
| `<frozen importlib._bootstrap>` | `491` | `58,601 B` | `574` |
| `battle_solver.py` | `359` | `54,336 B` | `48` |
| `pjit.py` | `647` | `47,760 B` | `127` |
| `sharding_impls.py` | `191` | `47,400 B` | `601` |
| `pjit.py` | `556` | `46,848 B` | `125` |
