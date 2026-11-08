"""Detect upgrade-blocking hardcodes before a real upgrade.

Scans C# host, Go relay, Python brain, JS client adapter and ini config for
literals that work on this machine but break when Ankama rotates endpoints,
bumps the bundle, or the software installs elsewhere. A guard: it fails loudly
and names the file + literal so the fix target is one edit away.

Run: python tools/test_hardcoded_upgrade_values.py   (exit 0 = green)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def fail(label: str, detail: str) -> None:
    FAILURES.append(f"{label}: {detail}")
    print(f"FAIL {label}: {detail}")


def ok(label: str) -> None:
    print(f"ok   {label}")


IPV4 = re.compile(
    r"\b(?!(?:127\.0\.0\.1|0\.0\.0\.0|255\.255\.255\.255))\d{1,3}\.\d{1,3}\."
    r"\d{1,3}\.\d{1,3}\b"
)
PORT_LITERAL = re.compile(
    r"\b(8880|8890|8891|8893|8894|8895|8896|8897|8898|8899|40000|5554)\b"
)
ANKAMA_HOST = re.compile(
    r"dt-proxy-production-login\.ankama-games\.com|auth\.ankama\.com"
)
UA_VERSION = re.compile(r"Chrome/\d+\.\d+\.\d+\.\d+")
VK_CODE = re.compile(r"\b(116|117|118|119|123)\b")
LOCALAPPDATA_PATH = re.compile(r"%LOCALAPPDATA%|\\DofusFarm\\webview2_profiles\\")


def scan_text(path: Path, label: str, patterns: list[tuple[re.Pattern, str]]) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for pat, what in patterns:
        for m in pat.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            snippet = text[max(0, m.start() - 40): m.end() + 40].replace("\n", "\\n")
            fail(label, f"{path}:{line_no}: {what}={m.group(0)!r} in {snippet!r}")


def scan_tree(roots: list[Path], patterns: list[tuple[re.Pattern, str]],
              globs: tuple[str, ...], label: str) -> None:
    for root in roots:
        if not root.exists():
            continue
        for pattern in globs:
            for path in root.rglob(pattern):
                if path.name == Path(__file__).name:
                    continue
                scan_text(path, label, patterns)


scan_tree([REPO / "desktop"], [(IPV4, "hardcoded IPv4"),
                               (PORT_LITERAL, "hardcoded port"),
                               (UA_VERSION, "frozen Chrome UA"),
                               (VK_CODE, "magic virtual-key code"),
                               (LOCALAPPDATA_PATH, "hardcoded profile path")],
          ("*.cs",), "C# host hardcode")
scan_tree([REPO / "scripts"], [(IPV4, "hardcoded IPv4"),
                               (PORT_LITERAL, "hardcoded port"),
                               (ANKAMA_HOST, "hardcoded Ankama login host")],
          ("*.go",), "Go relay hardcode")
scan_tree([REPO / "dofus-network"], [(PORT_LITERAL, "hardcoded port"),
                                     (ANKAMA_HOST, "hardcoded Ankama host")],
          ("*.py",), "Python agent hardcode")
scan_tree([REPO / "scripts", REPO / "desktop" / "client" / "www"],
          [(ANKAMA_HOST, "hardcoded Ankama host")], ("*.js",), "JS adapter hardcode")
scan_tree([REPO], [(IPV4, "hardcoded IPv4"), (ANKAMA_HOST, "hardcoded Ankama host")],
          ("*.ini",), "ini config hardcode")


# knowledge cache coverage (KNOWN_GAPS.md G29)
def check_knowledge_cache() -> None:
    cache = REPO / "dofus-network" / "data" / "knowledge" / "client_index.json"
    if not cache.exists():
        ok("knowledge cache absent (coverage check skipped)")
        return
    import json
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        ok("knowledge cache unparseable (coverage check skipped)")
        return
    rows = data.get("rows", {}) if isinstance(data, dict) else {}
    if isinstance(rows, dict) and rows:
        ids = [int(k.split(":")[0]) for k in rows.keys() if ":" in k and k.split(":")[0].isdigit()]
    else:
        quests = data.get("quests", data) if isinstance(data, dict) else data
        if not isinstance(quests, dict):
            ok("knowledge cache shape unexpected (coverage check skipped)")
            return
        ids = [int(k) for k in quests.keys() if str(k).isdigit()]
    if not ids:
        ok("knowledge cache has no numeric quest ids (coverage check skipped)")
        return
    max_id = max(ids)
    if max_id <= 2546:
        fail("knowledge-cache coverage", f"max cached quest id {max_id} <= 2,546 "
             "while live quest ids exceed it (G29)")
    else:
        ok(f"knowledge cache reaches quest id {max_id} (> 2,546)")


check_knowledge_cache()


print()
if FAILURES:
    print(f"{len(FAILURES)} hardcode(s) still present — fix before upgrading.")
    sys.exit(1)
print("no upgrade-blocking hardcodes detected")
sys.exit(0)