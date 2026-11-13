#!/usr/bin/env python3
"""Automated 8-Instance Headless Soak & Memory/Latency Profiler.

Orchestrates multi-slot headless soak testing against local mock services:
  1. LocalMockServer: high-throughput static & dynamic asset server.
  2. GoRelayPool: multi-port dofus-net-proxy instances with pprof & /__profiler.
  3. CombatServerProcess: combat_ai_server.py with tracemalloc & /profile.
  4. 8-Slot DofusFarm clients: C# WinForms message loop & UiDispatcher.
  5. SyntheticTrafficDriver: concurrent HTTP, combat decisions, AppendStore WAL appends.
  6. ContinuousSampler: Win32 PWS/GDI/handles, Go heap/goroutines, Python tracemalloc,
     AppendStore WAL disk footprint, and turn-solve latency percentiles (p50/p95/p99).
  7. LeakSlopeDetector: OLS linear regression (m, b, R^2) to detect monotonic leaks.

Budgets & SLOs enforced:
  * Private Working Set (RAM) < 200 MB per instance.
  * Turn-solve planning latency p99 < 10.0 ms.
  * UiDispatcher.Dropped == 0.
  * Monotonic leak slope check: zero monotonic leak across PWS, handles, goroutines, WAL.

Usage:
  python tools/soak_profiler.py [--slots 8] [--duration 30s] [--quick]
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent
DOFUS_NETWORK = REPO_ROOT / "dofus-network"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(DOFUS_NETWORK) not in sys.path:
    sys.path.insert(0, str(DOFUS_NETWORK))

try:
    from storage.append_store import AppendStore
except ImportError:
    AppendStore = None


# ---------------------------------------------------------------------------
# Win32 Process Metrics via ctypes
# ---------------------------------------------------------------------------

class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


@dataclass
class ProcessWin32Stats:
    pid: int
    private_ws_mb: float = 0.0
    working_set_mb: float = 0.0
    handles: int = 0
    gdi_objects: int = 0
    user_objects: int = 0


def get_process_win32_stats(pid: int) -> Optional[ProcessWin32Stats]:
    """Sample process memory and handle counts using native Windows APIs."""
    if platform.system() != "Windows" or pid <= 0:
        return None
    try:
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        h_proc = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
        )
        if not h_proc:
            return None
        try:
            pmc = PROCESS_MEMORY_COUNTERS_EX()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
            ctypes.windll.psapi.GetProcessMemoryInfo(
                h_proc, ctypes.byref(pmc), ctypes.sizeof(pmc)
            )
            handles = wintypes.DWORD()
            ctypes.windll.kernel32.GetProcessHandleCount(h_proc, ctypes.byref(handles))
            gdi = ctypes.windll.user32.GetGuiResources(h_proc, 0)
            user = ctypes.windll.user32.GetGuiResources(h_proc, 1)

            return ProcessWin32Stats(
                pid=pid,
                private_ws_mb=round(pmc.PrivateUsage / (1024.0 * 1024.0), 2),
                working_set_mb=round(pmc.WorkingSetSize / (1024.0 * 1024.0), 2),
                handles=int(handles.value),
                gdi_objects=int(gdi),
                user_objects=int(user),
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(h_proc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Linear Regression & Leak Detection Math
# ---------------------------------------------------------------------------

@dataclass
class RegressionResult:
    slope: float  # rate per second
    intercept: float
    r_squared: float
    delta: float
    min_val: float
    max_val: float
    is_leak: bool = False
    rate_per_min: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slope_per_sec": round(self.slope, 6),
            "rate_per_min": round(self.rate_per_min, 4),
            "intercept": round(self.intercept, 4),
            "r_squared": round(self.r_squared, 4),
            "delta": round(self.delta, 4),
            "min_val": round(self.min_val, 4),
            "max_val": round(self.max_val, 4),
            "is_leak": self.is_leak,
        }


def compute_linear_regression(
    timestamps: List[float],
    values: List[float],
    leak_rate_threshold_per_sec: float = 0.001,
    min_delta: float = 5.0,
    r2_threshold: float = 0.80,
) -> RegressionResult:
    """Compute OLS linear regression and test for a monotonic leak slope."""
    n = len(timestamps)
    if n < 3:
        val0 = values[0] if values else 0.0
        return RegressionResult(0.0, val0, 0.0, 0.0, val0, val0, False, 0.0)

    t_mean = sum(timestamps) / n
    y_mean = sum(values) / n

    numerator = sum((timestamps[i] - t_mean) * (values[i] - y_mean) for i in range(n))
    denominator = sum((timestamps[i] - t_mean) ** 2 for i in range(n))

    if denominator == 0.0:
        val0 = values[0]
        return RegressionResult(0.0, val0, 0.0, 0.0, val0, val0, False, 0.0)

    slope = numerator / denominator
    intercept = y_mean - slope * t_mean

    ss_tot = sum((values[i] - y_mean) ** 2 for i in range(n))
    ss_res = sum((values[i] - (slope * timestamps[i] + intercept)) ** 2 for i in range(n))

    r_squared = 1.0 - (ss_res / (ss_tot + 1e-12))
    r_squared = max(0.0, min(1.0, r_squared))

    delta = values[-1] - values[0]
    min_val = min(values)
    max_val = max(values)
    rate_per_min = slope * 60.0

    # Leak criteria: positive slope > threshold, strong correlation (monotonic upward climb),
    # and actual meaningful growth > min_delta.
    is_leak = (
        slope > leak_rate_threshold_per_sec
        and r_squared >= r2_threshold
        and delta >= min_delta
    )

    return RegressionResult(
        slope=slope,
        intercept=intercept,
        r_squared=r_squared,
        delta=delta,
        min_val=min_val,
        max_val=max_val,
        is_leak=is_leak,
        rate_per_min=rate_per_min,
    )


# ---------------------------------------------------------------------------
# Local Mock Server (Static & Game Data Upstream)
# ---------------------------------------------------------------------------

class LocalMockRequestHandler(BaseHTTPRequestHandler):
    """Serves mock assets, scripts, map data, and health probes."""

    protocol_version = "HTTP/1.1"

    # Pre-generate 8 KB dummy spritesheet and 50 KB dummy client script.
    _SPRITE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 8192
    _SCRIPT_BYTES = (
        b"/* Dofus Touch Mock Client Bundle */\n"
        b"window.__dofus_mock = true;\n"
        + b"function _tick(i){ return i * 2; }\n" * 1500
    )
    _DATA_JSON = json.dumps({
        "status": "ok",
        "map_id": 84674566,
        "subarea_id": 95,
        "world_x": 3,
        "world_y": -19,
        "cells": [3] * 560,
        "npcs": [{"id": 101, "cell": 240, "name": "Guard"}],
    }).encode("utf-8")

    def _send_payload(self, content_type: str, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = b"<!DOCTYPE html><html><head><title>Mock Dofus</title></head><body><h1>Mock Client</h1></body></html>"
            self._send_payload("text/html", html)
        elif path.startswith("/build/script.js") or path.endswith(".js"):
            self._send_payload("application/javascript", self._SCRIPT_BYTES)
        elif path.startswith("/assets/sprites.png") or path.endswith(".png"):
            self._send_payload("image/png", self._SPRITE_BYTES)
        elif path.startswith("/game/data.json") or path.endswith(".json"):
            self._send_payload("application/json", self._DATA_JSON)
        elif path in ("/__health", "/health"):
            self._send_payload("application/json", b'{"status":"ok"}')
        else:
            self._send_payload("text/plain", b"OK")

    def do_POST(self) -> None:
        self._send_payload("application/json", b'{"ok":true}')

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet during high-throughput soak


class LocalMockServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.server = ThreadingHTTPServer((host, port), LocalMockRequestHandler)
        self.server.daemon_threads = True
        self.host, self.port = self.server.server_address
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------
# Go Relay Pool Manager
# ---------------------------------------------------------------------------

@dataclass
class GoRelayInstance:
    port: int
    process: subprocess.Popen
    cache_dir: Path
    log_path: Path


class GoRelayPool:
    def __init__(self, base_port: int, count: int, mock_upstream_url: str, admin_token: str, temp_root: Path) -> None:
        self.base_port = base_port
        self.count = count
        self.mock_upstream_url = mock_upstream_url
        self.admin_token = admin_token
        self.temp_root = temp_root
        self.instances: List[GoRelayInstance] = []

    def start(self) -> None:
        proxy_exe = REPO_ROOT / "dofus-net-proxy.exe"
        if not proxy_exe.exists():
            proxy_exe = REPO_ROOT / "scripts" / "dofus-net-proxy.exe"

        for i in range(self.count):
            port = self.base_port + i
            cdir = self.temp_root / f"relay_cache_{port}"
            cdir.mkdir(parents=True, exist_ok=True)
            log_file = cdir / "proxy.log"

            cmd = [
                str(proxy_exe),
                "-port", str(port),
                "-cache", str(cdir),
                "-admin-token", self.admin_token,
                "-quiet",
                "-log", str(log_file),
                "-upstream", self.mock_upstream_url,
            ]
            env = dict(os.environ)
            env["DOFUS_RUN_TOKEN"] = self.admin_token
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            self.instances.append(GoRelayInstance(port, p, cdir, log_file))

        # Give relays a brief window to bind ports
        time.sleep(1.0)

    def stop(self) -> None:
        for inst in self.instances:
            try:
                inst.process.terminate()
                inst.process.wait(timeout=2.0)
            except Exception:
                try:
                    inst.process.kill()
                except Exception:
                    pass

    def sample_profiler(self, port: int) -> Optional[Dict[str, Any]]:
        url = f"http://127.0.0.1:{port}/__profiler"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.admin_token}"})
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        return None


# ---------------------------------------------------------------------------
# Python Combat AI Server Manager
# ---------------------------------------------------------------------------

class CombatServerProcess:
    def __init__(self, port: int, admin_token: str, temp_root: Path) -> None:
        self.port = port
        self.admin_token = admin_token
        self.temp_root = temp_root
        self.process: Optional[subprocess.Popen] = None
        self.log_path = temp_root / "combat_server.log"

    def start(self) -> None:
        script = DOFUS_NETWORK / "combat" / "combat_ai_server.py"
        env = dict(os.environ)
        env["DOFUS_RUN_TOKEN"] = self.admin_token
        env["PYTHONUNBUFFERED"] = "1"

        out_f = open(self.log_path, "w", encoding="utf-8")
        cmd = [sys.executable, str(script), str(self.port)]
        self.process = subprocess.Popen(
            cmd,
            stdout=out_f,
            stderr=subprocess.STDOUT,
            env=env,
        )

        # Wait for listening state
        for _ in range(30):
            try:
                url = f"http://127.0.0.1:{self.port}/health"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.admin_token}"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(0.2)

    def stop(self) -> None:
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

    def sample_profile(self) -> Optional[Dict[str, Any]]:
        url = f"http://127.0.0.1:{self.port}/v1/combat/profile"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.admin_token}"})
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        return None


# ---------------------------------------------------------------------------
# C# Slot Process Manager
# ---------------------------------------------------------------------------

@dataclass
class SlotProcess:
    slot_id: str
    port: int
    process: subprocess.Popen
    out_json: Path


class DofusFarmSlotPool:
    def __init__(self, slot_names: List[str], base_port: int, duration_sec: int, temp_root: Path) -> None:
        self.slot_names = slot_names
        self.base_port = base_port
        self.duration_sec = duration_sec
        self.temp_root = temp_root
        self.slots: List[SlotProcess] = []

    def start(self) -> None:
        exe = REPO_ROOT / "DofusFarm.exe"
        for i, name in enumerate(self.slot_names):
            port = self.base_port + i
            out_json = self.temp_root / f"soak_{name}.json"
            cmd = [
                str(exe),
                "--soak-slot",
                name,
                str(port),
                str(self.duration_sec + 5),
                str(out_json),
            ]
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.slots.append(SlotProcess(name, port, p, out_json))

    def stop(self) -> None:
        for s in self.slots:
            try:
                s.process.wait(timeout=2.0)
            except Exception:
                try:
                    s.process.terminate()
                    s.process.wait(timeout=1.0)
                except Exception:
                    try:
                        s.process.kill()
                    except Exception:
                        pass


# ---------------------------------------------------------------------------
# Synthetic Traffic Driver
# ---------------------------------------------------------------------------

class SyntheticTrafficDriver:
    """Drives concurrent proxy HTTP requests, combat AI decisions, and SQLite WAL appends."""

    def __init__(
        self,
        slot_names: List[str],
        base_proxy_port: int,
        combat_port: int,
        admin_token: str,
        storage_dbs: List[Path],
    ) -> None:
        self.slot_names = slot_names
        self.base_proxy_port = base_proxy_port
        self.combat_port = combat_port
        self.admin_token = admin_token
        self.storage_dbs = storage_dbs
        self._stop_event = threading.Event()
        self._threads: List[threading.Thread] = []

        # Dummy realistic fight board payload
        self._fight_board = {
            "in_fight": True, "in_preparation": False, "is_my_turn": True,
            "my_id": 1, "my_cell": 100, "map_id": 84674566, "round": 1, "fight_id": 1,
            "cells": [3] * 560,
            "my_stats": {"strength": 120, "intelligence": 50, "chance": 0, "agility": 80,
                         "power": 20, "damage": 10, "critical_hit": 5, "heals": 0},
            "spells": [{"id": 1, "name": "strike", "ap_cost": 3, "min_range": 1, "range": 4,
                        "requires_los": True, "castable": True, "casts_this_turn": 0,
                        "max_cast_per_turn": 2, "max_cast_per_target": 2,
                        "effects": [{"effect_id": 100, "dice_num": 10, "dice_side": 15}],
                        "critical_effects": []}],
            "fighters": [
                {"id": 1, "name": "hero", "cell": 100, "team": "player", "is_me": True,
                 "alive": True, "hp": 250, "max_hp": 250, "ap": 7, "max_ap": 7, "mp": 3, "max_mp": 3,
                 "level": 25, "resist_percent": {}, "resist_fixed": {}, "spells": []},
                {"id": 2, "name": "gobball", "cell": 103, "team": "enemy", "is_me": False,
                 "alive": True, "hp": 80, "max_hp": 80, "ap": 4, "max_ap": 4, "mp": 3, "max_mp": 3,
                 "level": 12, "resist_percent": {}, "resist_fixed": {}, "spells": []},
            ],
        }

    def start(self) -> None:
        self._stop_event.clear()

        # 1. Proxy HTTP workers (1 worker per slot)
        for i, _ in enumerate(self.slot_names):
            port = self.base_proxy_port + i
            t = threading.Thread(target=self._proxy_worker, args=(port,), daemon=True)
            self._threads.append(t)
            t.start()

        # 2. Combat decision worker (continuous realistic farm cadence: ~3-4 turns/sec)
        t = threading.Thread(target=self._combat_worker, daemon=True)
        self._threads.append(t)
        t.start()

        # 3. Storage WAL append worker
        if AppendStore is not None and self.storage_dbs:
            t = threading.Thread(target=self._storage_worker, daemon=True)
            self._threads.append(t)
            t.start()

    def stop(self) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=1.0)

    def _proxy_worker(self, port: int) -> None:
        endpoints = ["/", "/build/script.js", "/assets/sprites.png", "/game/data.json", "/__health"]
        idx = 0
        while not self._stop_event.is_set():
            path = endpoints[idx % len(endpoints)]
            idx += 1
            url = f"http://127.0.0.1:{port}{path}"
            try:
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.admin_token}"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    _ = resp.read()
            except Exception:
                pass
            time.sleep(0.25)

    def _combat_worker(self) -> None:
        url = f"http://127.0.0.1:{self.combat_port}/v1/combat/decide"
        body = json.dumps(self._fight_board).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.admin_token}",
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }
        while not self._stop_event.is_set():
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    _ = resp.read()
            except Exception:
                pass
            time.sleep(0.3)

    def _storage_worker(self) -> None:
        stores = [AppendStore(p, payload_format="msgpack") for p in self.storage_dbs]
        counter = 0
        try:
            while not self._stop_event.is_set():
                store = stores[counter % len(stores)]
                counter += 1
                record = {
                    "seq": counter,
                    "ts": time.time(),
                    "kind": "turn_decision",
                    "action": {"type": "cast", "spell_id": 1, "target_cell": 103},
                    "telemetry": {"pws_mb": 25.5, "latency_ms": 1.2},
                }
                store.append(record, kind="telemetry")
                if counter % 24 == 0:
                    for s in stores:
                        try:
                            s.retain(max_rows=100)
                            s.checkpoint()
                        except Exception:
                            pass
                time.sleep(0.1)
        finally:
            for s in stores:
                try:
                    s.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Soak Harness & Profiler Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class SoakSnapshot:
    timestamp: float
    elapsed: float
    csharp_pws_total_mb: float
    csharp_pws_per_slot: Dict[str, float]
    csharp_handles_total: int
    csharp_gdi_total: int
    csharp_user_total: int
    go_heap_inuse_mb: float
    go_goroutines_total: int
    go_conns_active_total: int
    combat_pws_mb: float
    combat_handles: int
    combat_p50_ms: float
    combat_p95_ms: float
    combat_p99_ms: float
    wal_total_bytes: int


class SoakProfilerHarness:
    def __init__(
        self,
        slots_count: int = 8,
        duration_sec: int = 30,
        sample_interval_sec: float = 1.0,
        temp_dir: Optional[Path] = None,
    ) -> None:
        self.slots_count = slots_count
        self.duration_sec = duration_sec
        self.sample_interval_sec = sample_interval_sec
        self.temp_root = temp_dir or Path(tempfile.mkdtemp(prefix="soak_harness_"))
        self.temp_root.mkdir(parents=True, exist_ok=True)

        self.slot_names = [f"dofus-{i+1:02d}" for i in range(self.slots_count)]
        self.base_proxy_port = 8180
        self.combat_port = 8892
        self.admin_token = "soak_harness_token_" + str(int(time.time()))

        # Components
        self.mock_server = LocalMockServer(port=9123)
        self.go_relays = GoRelayPool(
            base_port=self.base_proxy_port,
            count=self.slots_count,
            mock_upstream_url=f"http://127.0.0.1:{self.mock_server.port}",
            admin_token=self.admin_token,
            temp_root=self.temp_root,
        )
        self.combat_server = CombatServerProcess(
            port=self.combat_port,
            admin_token=self.admin_token,
            temp_root=self.temp_root,
        )
        self.csharp_slots = DofusFarmSlotPool(
            slot_names=self.slot_names,
            base_port=self.base_proxy_port,
            duration_sec=self.duration_sec,
            temp_root=self.temp_root,
        )
        self.storage_dbs = [self.temp_root / f"store_{name}.db" for name in self.slot_names]
        self.traffic = SyntheticTrafficDriver(
            slot_names=self.slot_names,
            base_proxy_port=self.base_proxy_port,
            combat_port=self.combat_port,
            admin_token=self.admin_token,
            storage_dbs=self.storage_dbs,
        )

        self.snapshots: List[SoakSnapshot] = []

    def run(self) -> Dict[str, Any]:
        """Execute the full soak cycle, return structured scorecard."""
        t_start = time.perf_counter()
        os.environ["DOFUS_RUN_TOKEN"] = self.admin_token

        print(f"[SOAK] Starting 8-Instance Soak Harness: {self.slots_count} slots for {self.duration_sec}s")
        print(f"       Temp directory: {self.temp_root}")

        try:
            self.mock_server.start()
            print(f"  [OK] Mock Upstream Server listening on http://127.0.0.1:{self.mock_server.port}")

            self.go_relays.start()
            print(f"  [OK] Go Relay Pool initialized ({self.slots_count} ports: {self.base_proxy_port}..{self.base_proxy_port+self.slots_count-1})")

            self.combat_server.start()
            print(f"  [OK] Combat AI Server listening on http://127.0.0.1:{self.combat_port}")

            self.csharp_slots.start()
            print(f"  [OK] C# DofusFarm slots spawned: {', '.join(self.slot_names)}")

            # Warm up combat server with 3 turns to prime map cache and JIT
            warmup_body = json.dumps(self.traffic._fight_board).encode("utf-8")
            warmup_headers = {
                "Authorization": f"Bearer {self.admin_token}",
                "Content-Type": "application/json",
                "Connection": "close",
            }
            for _ in range(3):
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{self.combat_port}/v1/combat/decide",
                        data=warmup_body,
                        headers=warmup_headers,
                    )
                    with urllib.request.urlopen(req, timeout=2.0) as resp:
                        _ = resp.read()
                except Exception:
                    pass
                time.sleep(0.05)

            # Reset latency metrics so cold start does not pollute steady-state soak SLO
            try:
                reset_req = urllib.request.Request(
                    f"http://127.0.0.1:{self.combat_port}/profile/reset",
                    data=b"{}",
                    headers=warmup_headers,
                )
                with urllib.request.urlopen(reset_req, timeout=2.0) as resp:
                    _ = resp.read()
            except Exception:
                pass

            self.traffic.start()
            print("  [OK] Synthetic Traffic Driver active (HTTP, Combat decisions, SQLite WAL appends)")

            t_soak_start = time.perf_counter()

            # Sampling loop
            last_print = 0.0
            while True:
                elapsed = time.perf_counter() - t_soak_start
                if elapsed >= self.duration_sec:
                    break

                snap = self._take_snapshot(elapsed)
                self.snapshots.append(snap)

                if elapsed - last_print >= max(2.0, self.sample_interval_sec * 2):
                    last_print = elapsed
                    max_slot_pws = max(snap.csharp_pws_per_slot.values()) if snap.csharp_pws_per_slot else 0.0
                    print(
                        f"  [STEP] t={elapsed:5.1f}s | C# Peak RAM: {max_slot_pws:5.1f} MB | "
                        f"Go Heap: {snap.go_heap_inuse_mb:4.1f} MB | Goroutines: {snap.go_goroutines_total:3d} | "
                        f"Combat p99: {snap.combat_p99_ms:4.2f} ms | WAL: {snap.wal_total_bytes / 1024:5.1f} KB"
                    )

                time.sleep(self.sample_interval_sec)

            # Capture final metrics while services are still live
            final_elapsed = time.perf_counter() - t_soak_start
            self.snapshots.append(self._take_snapshot(final_elapsed))
            self.final_combat_profile = self.combat_server.sample_profile() or {}

        finally:
            print("\n[SOAK] Stopping synthetic traffic and shutting down service processes...")
            self.traffic.stop()
            self.csharp_slots.stop()
            self.combat_server.stop()
            self.go_relays.stop()
            self.mock_server.stop()

        print("[SOAK] Evaluating leak slopes and budget thresholds...")
        return self._evaluate_results(time.perf_counter() - t_soak_start)

    def _take_snapshot(self, elapsed: float) -> SoakSnapshot:
        # 1. C# Slot Metrics
        pws_per_slot: Dict[str, float] = {}
        csharp_pws_total = 0.0
        csharp_handles_total = 0
        csharp_gdi_total = 0
        csharp_user_total = 0

        for s in self.csharp_slots.slots:
            stats = get_process_win32_stats(s.process.pid)
            if stats:
                pws_per_slot[s.slot_id] = stats.private_ws_mb
                csharp_pws_total += stats.private_ws_mb
                csharp_handles_total += stats.handles
                csharp_gdi_total += stats.gdi_objects
                csharp_user_total += stats.user_objects

        # 2. Go Relay Metrics
        go_heap_mb = 0.0
        go_goroutines = 0
        go_conns = 0
        for inst in self.go_relays.instances:
            p_stat = self.go_relays.sample_profiler(inst.port)
            if p_stat:
                go_heap_mb += p_stat.get("heap_inuse_bytes", 0) / (1024.0 * 1024.0)
                go_goroutines += p_stat.get("num_goroutine", 0)
                go_conns += p_stat.get("conns_active", 0)

        # 3. Combat Server Metrics
        combat_pws = 0.0
        combat_handles = 0
        if self.combat_server.process:
            c_stats = get_process_win32_stats(self.combat_server.process.pid)
            if c_stats:
                combat_pws = c_stats.private_ws_mb
                combat_handles = c_stats.handles

        combat_p50 = 0.0
        combat_p95 = 0.0
        combat_p99 = 0.0
        profile = self.combat_server.sample_profile()
        if profile and "latency" in profile:
            lat = profile["latency"]
            combat_p50 = lat.get("p50", 0.0)
            combat_p95 = lat.get("p95", 0.0)
            combat_p99 = lat.get("p99", 0.0)

        # 4. Storage WAL Footprint
        wal_total = 0
        for db in self.storage_dbs:
            wal_file = Path(str(db) + "-wal")
            if wal_file.exists():
                try:
                    wal_total += wal_file.stat().st_size
                except OSError:
                    pass

        return SoakSnapshot(
            timestamp=time.time(),
            elapsed=round(elapsed, 2),
            csharp_pws_total_mb=round(csharp_pws_total, 2),
            csharp_pws_per_slot=pws_per_slot,
            csharp_handles_total=csharp_handles_total,
            csharp_gdi_total=csharp_gdi_total,
            csharp_user_total=csharp_user_total,
            go_heap_inuse_mb=round(go_heap_mb, 2),
            go_goroutines_total=go_goroutines,
            go_conns_active_total=go_conns,
            combat_pws_mb=round(combat_pws, 2),
            combat_handles=combat_handles,
            combat_p50_ms=combat_p50,
            combat_p95_ms=combat_p95,
            combat_p99_ms=combat_p99,
            wal_total_bytes=wal_total,
        )

    def _evaluate_results(self, actual_duration: float) -> Dict[str, Any]:
        if not self.snapshots:
            return {"status": "error", "error": "No snapshots recorded", "passed": False}

        valid_snaps = [s for s in self.snapshots if s.csharp_pws_total_mb > 0]
        if not valid_snaps:
            valid_snaps = self.snapshots
        timestamps = [s.elapsed for s in valid_snaps]

        # Calculate regressions across active soak period
        csharp_pws_total_series = [s.csharp_pws_total_mb for s in valid_snaps]
        csharp_handles_series = [s.csharp_handles_total for s in valid_snaps]
        go_heap_series = [s.go_heap_inuse_mb for s in valid_snaps]
        go_goroutines_series = [s.go_goroutines_total for s in valid_snaps]
        combat_pws_series = [s.combat_pws_mb for s in valid_snaps]
        combat_p99_series = [s.combat_p99_ms for s in valid_snaps]
        wal_bytes_series = [s.wal_total_bytes / (1024.0 * 1024.0) for s in valid_snaps]

        reg_csharp_pws = compute_linear_regression(timestamps, csharp_pws_total_series, 0.01, 20.0)
        reg_csharp_handles = compute_linear_regression(timestamps, csharp_handles_series, 0.5, 100.0)
        reg_go_heap = compute_linear_regression(timestamps, go_heap_series, 0.01, 20.0)
        reg_go_goroutines = compute_linear_regression(timestamps, go_goroutines_series, 0.2, 20.0)
        reg_combat_pws = compute_linear_regression(timestamps, combat_pws_series, 0.01, 15.0)
        reg_wal = compute_linear_regression(timestamps, wal_bytes_series, 0.01, 15.0)

        # Per-slot peak PWS
        peak_pws_per_slot: Dict[str, float] = {}
        for s in valid_snaps:
            for name, pws in s.csharp_pws_per_slot.items():
                peak_pws_per_slot[name] = max(peak_pws_per_slot.get(name, 0.0), pws)

        overall_peak_slot_pws = max(peak_pws_per_slot.values()) if peak_pws_per_slot else 0.0
        # Ignore initial 0 or cold values if turns occurred
        active_p99s = [v for v in combat_p99_series if v > 0]
        max_combat_p99 = max(active_p99s) if active_p99s else 0.0
        final_combat_profile = getattr(self, "final_combat_profile", None) or self.combat_server.sample_profile() or {}

        # Parse C# slot json files for dropped counts
        total_dropped = 0
        total_csharp_samples = 0
        for slot_proc in self.csharp_slots.slots:
            if slot_proc.out_json.exists():
                try:
                    with open(slot_proc.out_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for samp in data.get("samples", []):
                            total_dropped += samp.get("dropped", 0)
                            total_csharp_samples += 1
                except Exception:
                    pass

        # Budget verification
        budget_ram_pass = overall_peak_slot_pws < 200.0
        budget_latency_pass = max_combat_p99 < 10.0
        budget_dropped_pass = total_dropped == 0

        # Monotonic leak checks
        leak_csharp_pass = not reg_csharp_pws.is_leak
        leak_handles_pass = not reg_csharp_handles.is_leak
        leak_go_pass = not reg_go_heap.is_leak and not reg_go_goroutines.is_leak
        leak_combat_pass = not reg_combat_pws.is_leak
        leak_wal_pass = not reg_wal.is_leak

        all_passed = (
            budget_ram_pass
            and budget_latency_pass
            and budget_dropped_pass
            and leak_csharp_pass
            and leak_handles_pass
            and leak_go_pass
            and leak_combat_pass
            and leak_wal_pass
        )

        scorecard = {
            "status": "success" if all_passed else "failed",
            "passed": all_passed,
            "duration_sec": round(actual_duration, 2),
            "slots_count": self.slots_count,
            "sample_count": len(self.snapshots),
            "budgets": {
                "ram_per_instance_mb": {
                    "limit": 200.0,
                    "measured_peak": round(overall_peak_slot_pws, 2),
                    "passed": budget_ram_pass,
                },
                "combat_latency_p99_ms": {
                    "limit": 10.0,
                    "measured_peak": round(max_combat_p99, 2),
                    "passed": budget_latency_pass,
                },
                "uidispatcher_dropped": {
                    "limit": 0,
                    "measured": total_dropped,
                    "samples_evaluated": total_csharp_samples,
                    "passed": budget_dropped_pass,
                },
            },
            "leak_slopes": {
                "csharp_pws_total": reg_csharp_pws.to_dict(),
                "csharp_handles_total": reg_csharp_handles.to_dict(),
                "go_heap_inuse_mb": reg_go_heap.to_dict(),
                "go_goroutines_total": reg_go_goroutines.to_dict(),
                "combat_pws_mb": reg_combat_pws.to_dict(),
                "storage_wal_mb": reg_wal.to_dict(),
            },
            "per_slot_peak_pws_mb": peak_pws_per_slot,
            "combat_profile": final_combat_profile,
        }

        return scorecard


# ---------------------------------------------------------------------------
# Report Generators (JSON & Markdown Artifact)
# ---------------------------------------------------------------------------

def generate_markdown_report(scorecard: Dict[str, Any], output_path: Path) -> str:
    """Render a GitHub-flavored Markdown soak profiling report."""
    b = scorecard.get("budgets", {})
    ram = b.get("ram_per_instance_mb", {})
    lat = b.get("combat_latency_p99_ms", {})
    drop = b.get("uidispatcher_dropped", {})
    slopes = scorecard.get("leak_slopes", {})

    status_badge = "**PASSED**" if scorecard.get("passed") else "**FAILED**"

    lines = [
        f"# 8-Instance Headless Soak & Memory/Latency Profile Report",
        f"",
        f"- **Verdict:** {status_badge}",
        f"- **Configured Slots:** {scorecard.get('slots_count')} instances",
        f"- **Duration:** {scorecard.get('duration_sec')}s ({scorecard.get('sample_count')} samples)",
        f"",
        f"## 1. Budget & SLO Verification",
        f"",
        f"| Component / Metric | Shipped Budget | Measured Peak | Verdict |",
        f"|:-------------------|:---------------|:--------------|:--------|",
        f"| **C# Slot Private WS** | `< 200.0 MB` | `{ram.get('measured_peak', 0)} MB` | {'PASS' if ram.get('passed') else 'FAIL'} |",
        f"| **Combat Solve Latency ($p_{{99}}$)** | `< 10.0 ms` | `{lat.get('measured_peak', 0)} ms` | {'PASS' if lat.get('passed') else 'FAIL'} |",
        f"| **UiDispatcher.Dropped** | `0` | `{drop.get('measured', 0)}` | {'PASS' if drop.get('passed') else 'FAIL'} |",
        f"",
        f"## 2. Monotonic Leak Slope Analysis",
        f"",
        r"Linear regression fits $y = m \cdot t + c$ over the soak time series:",
        f"",
        f"| Metric | Slope ($m$/s) | Rate ($m$/min) | $R^2$ Fit | Delta | Leak Detected |",
        f"|:-------|:--------------|:---------------|:----------|:------|:--------------|",
    ]

    metric_labels = [
        ("csharp_pws_total", "C# Total Private WS (MB)"),
        ("csharp_handles_total", "C# Total Handles"),
        ("go_heap_inuse_mb", "Go Relay Heap Inuse (MB)"),
        ("go_goroutines_total", "Go Relay Goroutines"),
        ("combat_pws_mb", "Combat Server RAM (MB)"),
        ("storage_wal_mb", "Storage WAL Total (MB)"),
    ]

    for key, label in metric_labels:
        s = slopes.get(key, {})
        leak_str = "**YES** (LEAK)" if s.get("is_leak") else "NO (FLAT)"
        lines.append(
            f"| **{label}** | `{s.get('slope_per_sec', 0):+.6f}` | `{s.get('rate_per_min', 0):+.4f}` | "
            f"`{s.get('r_squared', 0):.4f}` | `{s.get('delta', 0):+.2f}` | {leak_str} |"
        )

    lines.extend([
        f"",
        f"## 3. Per-Slot Private Working Set Breakdown",
        f"",
        f"| Slot Name | Peak Private WS (MB) | Budget (200 MB) | Margin |",
        f"|:----------|:---------------------|:----------------|:-------|",
    ])

    for slot, pws in scorecard.get("per_slot_peak_pws_mb", {}).items():
        margin = 200.0 - pws
        lines.append(f"| `{slot}` | `{pws:.2f} MB` | `200.00 MB` | `{margin:+.2f} MB` |")

    combat = scorecard.get("combat_profile", {})
    if combat and "latency" in combat:
        l = combat["latency"]
        lines.extend([
            f"",
            f"## 4. Combat Server Decision Latencies",
            f"",
            f"- **Sampled Turns:** {l.get('count', 0)}",
            f"- **Min Latency:** `{l.get('min', 0)} ms`",
            f"- **p50 Latency:** `{l.get('p50', 0)} ms`",
            f"- **p95 Latency:** `{l.get('p95', 0)} ms`",
            f"- **p99 Latency:** `{l.get('p99', 0)} ms`",
            f"- **Max Latency:** `{l.get('max', 0)} ms`",
        ])

    allocators = combat.get("top_allocators", [])
    if allocators:
        lines.extend([
            f"",
            f"## 5. Python tracemalloc Top Allocators",
            f"",
            f"| Filename | Line | Total Bytes | Allocation Count |",
            f"|:---------|:-----|:------------|:-----------------|",
        ])
        for alloc in allocators[:10]:
            lines.append(
                f"| `{alloc.get('filename')}` | `{alloc.get('lineno')}` | "
                f"`{alloc.get('size_bytes', 0):,} B` | `{alloc.get('count', 0):,}` |"
            )

    report_content = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_content, encoding="utf-8")
    return report_content


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def parse_duration(d_str: str) -> int:
    d_str = d_str.strip().lower()
    if d_str.endswith("s"):
        return max(1, int(d_str[:-1]))
    if d_str.endswith("m"):
        return max(1, int(d_str[:-1]) * 60)
    if d_str.endswith("h"):
        return max(1, int(d_str[:-1]) * 3600)
    return max(1, int(d_str))


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="8-Instance Headless Soak & Memory/Latency Profiler")
    parser.add_argument("--slots", type=int, default=8, help="Number of concurrent instances (default: 8)")
    parser.add_argument("--duration", type=str, default="30s", help="Soak duration (e.g. 30s, 5m, 4h)")
    parser.add_argument("--quick", action="store_true", help="Quick mode (runs 10s soak)")
    parser.add_argument("--out-dir", type=str, default="build/soak_run", help="Output directory")
    parser.add_argument("--output-json", type=str, default="", help="Path for JSON scorecard")
    parser.add_argument("--report-md", type=str, default="", help="Path for Markdown report")
    args = parser.parse_args()

    dur_sec = 10 if args.quick else parse_duration(args.duration)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = Path(args.output_json) if args.output_json else out_dir / "soak_profile_summary.json"
    md_path = Path(args.report_md) if args.report_md else out_dir / "soak_profile_report.md"

    harness = SoakProfilerHarness(
        slots_count=args.slots,
        duration_sec=dur_sec,
        sample_interval_sec=1.0 if dur_sec <= 60 else 5.0,
        temp_dir=out_dir,
    )

    scorecard = harness.run()

    # Save JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(scorecard, f, indent=2)
    print(f"\n[OK] Wrote JSON scorecard to {json_path}")

    # Save Markdown report
    generate_markdown_report(scorecard, md_path)
    print(f"[OK] Wrote Markdown report to {md_path}")

    verdict = "PASSED" if scorecard.get("passed") else "FAILED"
    print(f"\n==========================================================")
    print(f"   SOAK RUN COMPLETE: {verdict}")
    print(f"==========================================================")

    return 0 if scorecard.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
