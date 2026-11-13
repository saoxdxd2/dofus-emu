# 8-Instance Headless Soak & Memory/Latency Profile Report

- **Verdict:** **FAILED**
- **Configured Slots:** 8 instances
- **Duration:** 305.96s (60 samples)

## 1. Budget & SLO Verification

| Component / Metric | Shipped Budget | Measured Peak | Verdict |
|:-------------------|:---------------|:--------------|:--------|
| **C# Slot Private WS** | `< 200.0 MB` | `25.36 MB` | PASS |
| **Combat Solve Latency ($p_{99}$)** | `< 10.0 ms` | `21.45 ms` | FAIL |
| **UiDispatcher.Dropped** | `0` | `0` | PASS |

## 2. Monotonic Leak Slope Analysis

Linear regression fits $y = m \cdot t + c$ over the soak time series:

| Metric | Slope ($m$/s) | Rate ($m$/min) | $R^2$ Fit | Delta | Leak Detected |
|:-------|:--------------|:---------------|:----------|:------|:--------------|
| **C# Total Private WS (MB)** | `+0.023547` | `+1.4128` | `0.1789` | `+28.02` | NO (FLAT) |
| **C# Total Handles** | `-0.281750` | `-16.9050` | `0.7753` | `-88.00` | NO (FLAT) |
| **Go Relay Heap Inuse (MB)** | `+0.018101` | `+1.0861` | `0.0981` | `+16.23` | NO (FLAT) |
| **Go Relay Goroutines** | `+0.000706` | `+0.0424` | `0.0376` | `+0.00` | NO (FLAT) |
| **Combat Server RAM (MB)** | `+0.000568` | `+0.0341` | `0.1476` | `+0.30` | NO (FLAT) |
| **Storage WAL Total (MB)** | `+0.114547` | `+6.8728` | `0.9319` | `+31.21` | **YES** (LEAK) |

## 3. Per-Slot Private Working Set Breakdown

| Slot Name | Peak Private WS (MB) | Budget (200 MB) | Margin |
|:----------|:---------------------|:----------------|:-------|
| `dofus-01` | `25.26 MB` | `200.00 MB` | `+174.74 MB` |
| `dofus-02` | `25.27 MB` | `200.00 MB` | `+174.73 MB` |
| `dofus-03` | `25.36 MB` | `200.00 MB` | `+174.64 MB` |
| `dofus-04` | `25.31 MB` | `200.00 MB` | `+174.69 MB` |
| `dofus-05` | `25.28 MB` | `200.00 MB` | `+174.72 MB` |
| `dofus-06` | `25.34 MB` | `200.00 MB` | `+174.66 MB` |
| `dofus-07` | `25.27 MB` | `200.00 MB` | `+174.73 MB` |
| `dofus-08` | `25.31 MB` | `200.00 MB` | `+174.69 MB` |

## 4. Combat Server Decision Latencies

- **Sampled Turns:** 948
- **Min Latency:** `1.3 ms`
- **p50 Latency:** `2.7 ms`
- **p95 Latency:** `5.765 ms`
- **p99 Latency:** `13.066 ms`
- **Max Latency:** `39.2 ms`

## 5. Python tracemalloc Top Allocators

| Filename | Line | Total Bytes | Allocation Count |
|:---------|:-----|:------------|:-----------------|
| `<frozen importlib._bootstrap_external>` | `511` | `280,524 B` | `2,084` |
| `__init__.py` | `771` | `132,086 B` | `13` |
| `source_info_util.py` | `213` | `78,432 B` | `410` |
| `flattree.py` | `234` | `73,880 B` | `856` |
| `partial_eval.py` | `1841` | `70,040 B` | `169` |
| `<frozen importlib._bootstrap>` | `491` | `58,549 B` | `573` |
| `battle_solver.py` | `359` | `54,336 B` | `48` |
| `pjit.py` | `647` | `47,760 B` | `127` |
| `sharding_impls.py` | `191` | `47,400 B` | `601` |
| `pjit.py` | `556` | `46,848 B` | `125` |
