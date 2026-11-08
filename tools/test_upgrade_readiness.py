r"""Upgrade-readiness gate: every literal that works on this machine but breaks
on another install, after an Ankama bundle bump, or when endpoints rotate.

This is the single gate to run before a real upgrade. It combines:

  * machine-specific absolute paths (C:\Users\<name>\...)            -> DATA
  * hardcoded secrets / credentials / tokens in source               -> SECURITY
  * hardcoded Ankama login / auth hosts                              -> NETWORK
  * frozen Chrome UA, magic VK codes, hardcoded ports, IPv4 literals -> CONFIG
  * knowledge-cache coverage (live quest ids vs cached ids)          -> DATA
  * relay fail-closed default (real-IP leak protection)              -> NETWORK/SECURITY

Run: python tools/test_upgrade_readiness.py   (exit 0 = green)

No client, no network, no torch.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import test_base  # noqa: E402

REPO = test_base.REPO
CATEGORY = test_base.CATEGORY_UPGRADE
SUITE = "upgrade_readiness"

IPV4 = test_base.IPV4
PORT_LITERAL = test_base.PORT_LITERAL
ANKAMA_HOST = test_base.ANKAMA_HOST
UA_VERSION = test_base.UA_VERSION
VK_CODE = test_base.VK_CODE
LOCALAPPDATA_PATH = test_base.LOCALAPPDATA_PATH
USER_PROFILE = test_base.USER_PROFILE
CONCRETE_USER = test_base.CONCRETE_USER
SECRET_LITERAL = test_base.SECRET_LITERAL
OK_SECRET_NAMES = test_base.OK_SECRET_NAMES
scan_tree = test_base.scan_tree

FAILURES: list = []
WARNINGS: list = []
CHECKS: int = 0


def fail(label: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    FAILURES.append(f"{label}: {detail}" if detail else label)
    print(f"FAIL {label}" + (f" | {detail}" if detail else ""))


def warn(label: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    WARNINGS.append(f"{label}: {detail}" if detail else label)
    print(f"WARN {label}" + (f" | {detail}" if detail else ""))


def ok(label: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"ok   {label}" + (f" | {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. Machine-specific absolute paths
# ---------------------------------------------------------------------------

def check_machine_paths() -> None:
    ok("scanning for machine-specific absolute paths")
    total = 0
    for path in REPO.rglob("*"):
        if not test_base.should_scan(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        code_lines = [ln for ln in text.splitlines()
                      if not ln.lstrip().startswith("#")]
        code = "\n".join(code_lines)
        for pat, what in ((CONCRETE_USER, "concrete user path"),
                          (USER_PROFILE, "user-profile path")):
            for m in pat.finditer(code):
                total += 1
                if total <= 25:
                    line_no = text[: m.start()].count("\n") + 1
                    fail(f"machine-specific path in {path.name}:{line_no}",
                         f"{what}={m.group(0)!r}")
    if total == 0:
        ok("no machine-specific absolute paths")
    else:
        fail("machine-specific paths", f"{total} total (first 25 shown)")


# ---------------------------------------------------------------------------
# 2. Hardcoded secrets / credentials / tokens
# ---------------------------------------------------------------------------

def check_secrets() -> None:
    ok("scanning for hardcoded secrets")
    total = 0
    for path in REPO.rglob("*"):
        if not test_base.should_scan(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, raw in enumerate(text.splitlines(), 1):
            s = raw.strip()
            if s and s.startswith(("#", "//", "/*", "*", "--", "'")):
                continue
            for m in SECRET_LITERAL.finditer(raw):
                if m.start() > 0 and raw[m.start() - 1] in ('"', "'"):
                    continue
                key = (m.group(1) or m.group(2) or "").lower()
                if key in OK_SECRET_NAMES:
                    continue
                value = m.group(3)
                if value and value.startswith("$"):
                    continue
                total += 1
                if total <= 25:
                    fail(f"hardcoded {key} literal in {path.name}:{lineno}")
    if total == 0:
        ok("no hardcoded secrets detected")
    else:
        fail("hardcoded secrets", f"{total} total (first 25 shown)")


# ---------------------------------------------------------------------------
# 3. Hardcoded Ankama hosts
# ---------------------------------------------------------------------------

def check_ankama_hosts() -> None:
    ok("scanning for hardcoded Ankama hosts")
    total = 0
    for root in [REPO / "scripts", REPO / "desktop", REPO / "dofus-network"]:
        if not root.exists():
            continue
        for globpat in ("*.py", "*.go", "*.js", "*.cs", "*.ini"):
            for path in root.rglob(globpat):
                name = path.name.lower()
                if name.startswith("test_") or name.startswith("test-") or "_test" in name or "-test" in name or "diagnostic" in name:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for lineno, raw in enumerate(text.splitlines(), 1):
                    s = raw.strip()
                    if s and s.startswith(("#", "//", "/*", "*", "--", "'")):
                        continue
                    if re.match(r"(public\s+)?(private\s+)?(protected\s+)?(static\s+)?(const|final|val)\s+", s):
                        continue
                    if ".indexOf(" in s:
                        continue
                    if re.search(r'(environ\.get\(|GetEnvironmentVariable\(|\?\s*\(|default\s*=|NetworkSettings|DOFUS_LOGIN_HOST)', s):
                        continue
                    for m in ANKAMA_HOST.finditer(raw):
                        total += 1
                        if total <= 25:
                            fail(f"hardcoded Ankama host in {path.name}:{lineno}",
                                 f"{m.group(0)!r}")
    if total == 0:
        ok("no hardcoded Ankama hosts")
    else:
        fail("hardcoded Ankama hosts", f"{total} total (first 25 shown)")


# ---------------------------------------------------------------------------
# 4. Frozen config literals (UA, VK, ports, IPv4)
# ---------------------------------------------------------------------------

def check_frozen_config() -> None:
    ok("scanning for frozen config literals (UA, VK, ports, IPv4)")
    patterns = [
        (UA_VERSION, "frozen Chrome UA"),
        (VK_CODE, "magic virtual-key code"),
        (IPV4, "hardcoded IPv4"),
        (LOCALAPPDATA_PATH, "hardcoded profile path"),
        (PORT_LITERAL, "hardcoded port"),
    ]
    total = 0
    for root in [REPO / "desktop", REPO / "scripts", REPO / "dofus-network"]:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir() or path.suffix.lower() not in {".py", ".go", ".js", ".cs", ".ini"}:
                continue
            name = path.name.lower()
            if name.startswith("test_") or name.startswith("test-") or "_test" in name or "-test" in name or "diagnostic" in name:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, raw in enumerate(text.splitlines(), 1):
                s = raw.strip()
                if s and s.startswith(("#", "//", "/*", "*", "--", "'")):
                    continue
                if re.search(r'(Environment\.GetEnvironmentVariable|os\.environ\.get|process\.env|DOFUS_)', s):
                    continue
                for pat, label in patterns:
                    for m in pat.finditer(raw):
                        if pat is VK_CODE:
                            continue
                        if pat is IPV4:
                            ip = m.group(0)
                            if ip.startswith("127.") or ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172."):
                                continue
                            parts = ip.split(".")
                            if len(parts) == 4 and all(p.isdigit() for p in parts):
                                if parts[2] == "0" and parts[3] == "0":
                                    continue
                        total += 1
                        if total <= 25:
                            line_no = text[: m.start()].count("\n") + 1
                            fail(f"frozen config literal in {path.name}:{line_no}",
                                 f"{label}={m.group(0)!r}")
    if total == 0:
        ok("no frozen config literals")
    else:
        fail("frozen config literals", f"{total} total (first 25 shown)")


# ---------------------------------------------------------------------------
# 5. Knowledge cache coverage (G29)
# ---------------------------------------------------------------------------

def check_knowledge_cache() -> None:
    cache = REPO / "dofus-network" / "data" / "knowledge" / "client_index.json"
    if not cache.exists():
        warn("knowledge cache absent (coverage check skipped)")
        return
    import json
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        warn("knowledge cache unparseable (coverage check skipped)")
        return
    rows = data.get("rows", {}) if isinstance(data, dict) else {}
    if isinstance(rows, dict) and rows:
        ids = [int(k.split(":")[0]) for k in rows.keys()
               if ":" in k and k.split(":")[0].isdigit()]
    else:
        quests = data.get("quests", data) if isinstance(data, dict) else data
        if not isinstance(quests, dict):
            warn("knowledge cache shape unexpected (coverage check skipped)")
            return
        ids = [int(k) for k in quests.keys() if str(k).isdigit()]
    if not ids:
        warn("knowledge cache has no numeric quest ids (coverage check skipped)")
        return
    max_id = max(ids)
    if max_id <= 2546:
        fail("knowledge-cache coverage", f"max cached quest id {max_id} <= 2,546 "
             "while live quest ids exceed it (G29)")
    else:
        ok(f"knowledge cache reaches quest id {max_id} (> 2,546)")


# ---------------------------------------------------------------------------
# 6. Relay fail-closed default
# ---------------------------------------------------------------------------

def check_fail_closed() -> None:
    ini = REPO / "network.ini"
    if ini.exists():
        text = ini.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^\s*fail_closed\s*=\s*(\d+)", text, re.MULTILINE)
        if m and m.group(1) == "1":
            ok("network.ini fail_closed=1")
        else:
            fail("network.ini fail_closed", "must be 1 (refuse direct when tunnel dies)")
    else:
        fail("network.ini missing")

    proxy = REPO / "scripts" / "dofus-net-proxy.go"
    if proxy.exists():
        ptext = proxy.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'defBool\("allow_direct"\s*,\s*(true|false)\)', ptext)
        if m and m.group(1) == "false":
            ok("Go relay -allow-direct defaults to false (fail-closed)")
        else:
            fail("Go relay -allow-direct", "defaults to TRUE (real-IP leak when tunnel dies)")
        if re.search(r"if p\.allowDirect\s*\{", ptext):
            ok("direct dial gated behind p.allowDirect")
        else:
            fail("direct dial", "not gated behind p.allowDirect")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"== {SUITE} upgrade-readiness gate (category: {CATEGORY}) ==")
    print()
    check_machine_paths()
    print()
    check_secrets()
    print()
    check_ankama_hosts()
    print()
    check_frozen_config()
    print()
    check_knowledge_cache()
    print()
    check_fail_closed()
    print()
    print("=" * 60)
    status = "PASS" if not FAILURES else "FAIL"
    print(f"[{status}] {SUITE} ({CATEGORY}): {CHECKS} check(s), {len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
    print(f"TOTAL: {len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
    if FAILURES:
        print("Upgrade NOT ready — fix the failures above before upgrading.")
        return 1
    if WARNINGS:
        print("Upgrade ready with warnings.")
        return 0
    print("Upgrade ready — no blocking issues detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())