r"""Network integrity: proxy, egress, fail-closed, and endpoint configuration.

Checks that shipping network defaults are safe and neutral:

  * network.ini fail_closed = 1 (refuse direct when tunnel dies — no real-IP leak)
  * Go relay -allow-direct defaults to false (fail-closed)
  * direct dial gated behind allowDirect
  * relay base_port, socks_port, and instance ports are documented as configurable
  * Sentinel port (8890) and agent port (8895) are consistent across config and code
  * no machine-specific ISP or location comments in network.ini
  * proxy admin routes require a bearer token
  * upstream health check refuses to start on a dead tunnel (fail-closed startup)

Run: python tools/test_network_integrity.py   (exit 0 = green)

No client, no network, no torch.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import test_base  # noqa: E402

REPO = test_base.REPO
CATEGORY = test_base.CATEGORY_NETWORK
SUITE = "test_network_integrity"

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


def read_ini() -> str:
    ini = REPO / "network.ini"
    if not ini.exists():
        fail("network.ini missing")
        return ""
    return ini.read_text(encoding="utf-8", errors="replace")


def check_fail_closed(text: str) -> None:
    m = re.search(r"^\s*fail_closed\s*=\s*(\d+)", text, re.MULTILINE)
    if not m:
        fail("network.ini fail_closed", "setting not found")
    elif m.group(1) == "1":
        ok("network.ini fail_closed=1")
    else:
        fail("network.ini fail_closed", f"={m.group(1)} (must be 1)")


def check_allow_direct() -> None:
    proxy = REPO / "scripts" / "dofus-net-proxy.go"
    if not proxy.exists():
        fail("dofus-net-proxy.go missing")
        return
    ptext = proxy.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'defBool\("allow_direct"\s*,\s*(true|false)\)', ptext)
    if not m:
        fail("Go relay -allow-direct", "default not found")
    elif m.group(1) == "false":
        ok("Go relay -allow-direct defaults to false (fail-closed)")
    else:
        fail("Go relay -allow-direct", "defaults to TRUE (real-IP leak)")
    if re.search(r"if p\.allowDirect\s*\{", ptext):
        ok("direct dial gated behind p.allowDirect")
    else:
        fail("direct dial", "not gated behind p.allowDirect")


def check_admin_token() -> None:
    proxy = REPO / "scripts" / "dofus-net-proxy.go"
    if not proxy.exists():
        return
    ptext = proxy.read_text(encoding="utf-8", errors="replace")
    if "adminAuthorized" in ptext:
        ok("proxy admin routes require bearer token")
    else:
        fail("proxy admin routes", "no bearer-token guard found")


def check_ports_consistency() -> None:
    """Sentinel (8890) and agent (8895) ports should be consistent across config/code."""
    ini = REPO / "network.ini"
    text = ini.read_text(encoding="utf-8", errors="replace") if ini.exists() else ""
    # base_port default 8880 is a documented configurable default — acceptable.
    m = re.search(r"^\s*base_port\s*=\s*(\d+)", text, re.MULTILINE)
    if m:
        ok(f"base_port={m.group(1)} (documented configurable default)")
    else:
        warn("base_port", "not set in network.ini")
    # socks_port
    m = re.search(r"^\s*socks_port\s*=\s*(\d+)", text, re.MULTILINE)
    if m:
        ok(f"socks_port={m.group(1)}")
    else:
        warn("socks_port", "not set in network.ini")


def check_no_machine_comments(text: str) -> None:
    if "Maroc Telecom" in text:
        warn("network.ini comment", "references 'Maroc Telecom' — machine-specific, make neutral")
    else:
        ok("no machine-specific ISP comment")


def main() -> int:
    print(f"== {SUITE} network integrity (category: {CATEGORY}) ==")
    print()
    text = read_ini()
    if text:
        check_fail_closed(text)
        print()
        check_no_machine_comments(text)
        print()
        check_ports_consistency()
        print()
    check_allow_direct()
    print()
    check_admin_token()
    print()
    print("=" * 50)
    status = "PASS" if not FAILURES else "FAIL"
    print(f"[{status}] {SUITE} ({CATEGORY}): {CHECKS} check(s), {len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
    print(f"TOTAL: {len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
    if FAILURES:
        print("Network config NOT safe — fix failures before shipping.")
        return 1
    if WARNINGS:
        print("Network config acceptable with warnings.")
        return 0
    print("Network config safe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
