"""Drive the DofusFarm host from a script: launch every instance, read what is running.

Why UI Automation and not a new endpoint. The host (`desktop/mainform.cs`) draws its controls by
hand, so WinForms exposes them as unnamed panes with no InvokePattern — but UIA still reports each
one's `Name` and bounding rectangle, and a click at its centre is the same event a user produces.
That keeps this a *test-harness* concern: no C# change, no new network surface, nothing to rebuild
or reinstall. If the panel is redesigned, the names below are the only contract and the tool says
so loudly instead of silently clicking the wrong thing.

The controls this needs (measured 2026-10-27, host v3.14.3):
    "Launch All (5x)"   starts every configured instance, including the one with the saved login
    "0 / 5 Running"     the status line, which is how the tool knows boot is in progress

    python tools/host_control.py --list
    python tools/host_control.py --launch-all
    python tools/host_control.py --running
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_EXE = REPO_ROOT.parent / "DofusFarm.exe"
HOST_PROCESS = "DofusFarm"

# Read the tree once per call; PowerShell + UIA is ~1 s, and the panel is not on a hot path.
UIA_SCRIPT = r"""
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$proc = Get-Process DofusFarm -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { '[]'; exit }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$cands = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
if ($cands.Count -eq 0) { '[]'; exit }
# The game clients belong to the SAME process as the manager, so "the first window of the
# process" is whichever client happens to be on top — with four clients open that read
# returned 71 elements and no "Launch All", which looked like a click that never worked.
# MainWindowHandle is no help either: measured on this host it points at "[WebView2] Instance
# #5 - Dofus Farm". The manager is the only window whose title does not name an instance.
$win = $cands | Where-Object { $_.Current.Name -notmatch 'WebView2|Instance #' -and $_.Current.Name -match 'Farm|Manager' } | Select-Object -First 1
if (-not $win) {
    try {
        $hwnd = [int]$proc.MainWindowHandle
        $win = $cands | Where-Object { $_.Current.NativeWindowHandle -eq $hwnd } | Select-Object -First 1
    } catch {}
}
if (-not $win) { $win = $cands[0] }
$items = @()
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition)
foreach ($e in $all) {
    $r = $e.Current.BoundingRectangle
    if ($r.Width -le 0 -or $r.Height -le 0) { continue }
    $items += [pscustomobject]@{
        name  = $e.Current.Name
        type  = $e.Current.ControlType.ProgrammaticName
        x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height
    }
}
$items | ConvertTo-Json -Compress -Depth 3
"""


def elements() -> List[Dict[str, Any]]:
    """Every visible UIA element of the host window, with its screen rectangle."""
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", UIA_SCRIPT],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = (proc.stdout or "").strip()
    if not raw or raw == "[]":
        return []
    try:
        # strict=False: Windows PowerShell's ConvertTo-Json emits raw control characters from
        # panel text (a multi-line log line), which the default parser refuses — that made the
        # first version report "no host window" while the window was right there.
        parsed = json.loads(raw, strict=False)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def find(name: str, items: Optional[Sequence[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """The first element whose accessible name starts with `name` (case-insensitive)."""
    needle = name.strip().lower()
    for item in items if items is not None else elements():
        if str(item.get("name") or "").strip().lower().startswith(needle):
            return item
    return None


def _click(x: int, y: int) -> None:
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # left down
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # left up


def _host_hwnd(title_needle: str = "dofus") -> Optional[int]:
    """The first top-level window whose title contains the needle (case-insensitive)."""
    user32 = ctypes.windll.user32
    found: List[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _visit(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            text = buf.value.lower()
            if title_needle.lower() in text:
                found.append(int(hwnd))
        return True

    user32.EnumWindows(_visit, 0)
    return found[0] if found else None


def focus_host() -> Optional[int]:
    """Raise the host window before clicking.

    The first unattended attempt clicked the right pixel of the wrong window: another app held
    the foreground, so the synthetic click never reached the panel and every instance stayed
    stopped. A click is only equivalent to a user's click if the window is actually in front.
    """
    user32 = ctypes.windll.user32
    # "farm" alone also matches the game clients ("[WebView2] Instance #5 - Dofus Farm"), which
    # belong to the same process — raising one of those instead of the panel is how a click
    # lands on the wrong window.
    hwnd = _host_hwnd("farm manager") or _host_hwnd("manager") or _host_hwnd("farm")
    if hwnd is None:
        return None
    user32.ShowWindow(hwnd, 9)            # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    current = user32.GetForegroundWindow()
    if current != hwnd:
        # a foreground lock (the OS thinks another app owns focus) is bypassed by briefly
        # attaching to the foreground thread, which is what a real activation does
        fg_thread = user32.GetWindowThreadProcessId(current, None)
        my_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(my_thread, fg_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(my_thread, fg_thread, False)
    time.sleep(0.4)
    return hwnd


UIA_ALL_WINDOWS = r"""
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$proc = Get-Process DofusFarm -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { '[]'; exit }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$items = @()
foreach ($win in $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)) {
    $wtitle = $win.Current.Name
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                        [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($e in $all) {
        $r = $e.Current.BoundingRectangle
        if ($r.Width -le 0 -or $r.Height -le 0) { continue }
        $items += [pscustomobject]@{
            window = $wtitle; name = $e.Current.Name
            type = $e.Current.ControlType.ProgrammaticName
            x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height
        }
    }
}
$items | ConvertTo-Json -Compress -Depth 3
"""


def all_windows() -> List[Dict[str, Any]]:
    """Every element of every top-level host window, each tagged with its window title."""
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", UIA_ALL_WINDOWS],
                              capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = (proc.stdout or "").strip()
    if not raw or raw == "[]":
        return []
    try:
        parsed = json.loads(raw, strict=False)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def focus_window(needle: str) -> Optional[int]:
    """Raise the first window whose title contains `needle`."""
    user32 = ctypes.windll.user32
    hwnd = _host_hwnd(needle)
    if hwnd is None:
        return None
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    current = user32.GetForegroundWindow()
    if current != hwnd:
        fg_thread = user32.GetWindowThreadProcessId(current, None)
        my_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(my_thread, fg_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(my_thread, fg_thread, False)
    time.sleep(0.35)
    return hwnd


def dismiss_alerts(limit: int = 12) -> int:
    """Click OK on every browser alert ("dofus.local says ...").

    A JS alert blocks the page's own thread, so the injected agent cannot answer a directive
    while one is open — all five slots timed out with "Server busy" dialogs on screen. Clicking
    OK is exactly what the user does; the fix is to do it without a human.
    """
    clicked = 0
    for _ in range(limit):
        target = None
        items = all_windows()
        for item in items:
            if str(item.get("name") or "").strip().lower() != "ok":
                continue
            if "says" in str(item.get("window") or "").lower():
                target = item
                break
        if target is None:
            if clicked == 0:
                # "0 dismissed" is only useful if the next occurrence says why. A dialog whose
                # window is not titled "…says…" (or whose button is not named "OK") looks
                # exactly like a page with no dialog at all, and a frozen page then looks like a
                # dead client.
                titled = sorted({str(w.get("window") or "") for w in items
                                 if str(w.get("name") or "").strip().lower() == "ok"})
                if titled:
                    print("[host_control] no alert dismissed; OK buttons live under %s"
                          % titled[:6], file=sys.stderr, flush=True)
            break
        focus_window(str(target.get("window") or "says"))
        _click(int(target["x"]) + int(target["w"]) // 2,
               int(target["y"]) + int(target["h"]) // 2)
        clicked += 1
        time.sleep(0.8)
    return clicked


def click(name: str) -> bool:
    """Click the control named `name`, after bringing the host forward and re-reading its layout."""
    focus_host()
    item = find(name)
    if item is None:
        return False
    _click(int(item["x"]) + int(item["w"]) // 2, int(item["y"]) + int(item["h"]) // 2)
    return True


def running_counts(items: Optional[Sequence[Dict[str, Any]]] = None) -> Optional[Dict[str, int]]:
    """"N / M Running" from the status line — M is how many instances are configured.

    Reading without arguments raises the host first: after Launch All the game windows hold the
    foreground, and the panel's UIA tree then comes back empty (a minimised window reports no
    rectangles at all). That empty read made a click that *had* worked look unverified.
    """
    import re
    if items is None:
        focus_host()
        items = elements()
    for item in items:
        match = re.search(r"(\d+)\s*/\s*(\d+)\s+Running", str(item.get("name") or ""))
        if match:
            return {"running": int(match.group(1)), "configured": int(match.group(2))}
    return None


def host_running() -> bool:
    try:
        proc = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DofusFarm.exe"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return HOST_PROCESS.lower() in (proc.stdout or "").lower()


def start_host(wait_s: float = 40.0) -> bool:
    """Launch DofusFarm.exe and wait until its window answers UIA. False if it never does."""
    if host_running():
        return True
    if not HOST_EXE.is_file():
        return False
    subprocess.Popen([str(HOST_EXE)], cwd=str(HOST_EXE.parent),
                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if elements():
            return True
        time.sleep(2.0)
    return False


def _safe_target(item: Dict[str, Any], window: Optional[Dict[str, Any]]) -> bool:
    """Refuse a click that could land on chrome rather than the control.

    The first automated launch clicked a stale rectangle and hit the window's close button: the
    host shut all six instances down (`reason=UserClosing ... CloseAll`) instead of starting them.
    A control must sit inside its window with margin, so a mis-measured rectangle fails closed
    instead of destroying a session.
    """
    if window is None:
        return True
    left, top = int(window["x"]), int(window["y"])
    right = left + int(window["w"])
    bottom = top + int(window["h"])
    cx = int(item["x"]) + int(item["w"]) // 2
    cy = int(item["y"]) + int(item["h"]) // 2
    margin = 24
    if not (left + margin <= cx <= right - margin and top + margin <= cy <= bottom - margin):
        return False
    # the close/maximise cluster lives in the top-right corner of a Windows title bar
    if cy < top + 48 and cx > right - 140:
        return False
    return True


LIVE_READY = REPO_ROOT / "tools" / "live_ready.py"
# A launch into a dead egress is not a test: it is N native "Server busy" dialogs, each of which
# freezes its page. These preflight failures are the ones that mean "do not start instances".
_BLOCKING = ("egress", "stop file", "page build")


def preflight(force: bool = False) -> Dict[str, Any]:
    """Ask the release preflight whether a live run is possible at all."""
    try:
        proc = subprocess.run([sys.executable, str(LIVE_READY), "--json"],
                              capture_output=True, text=True, timeout=180)
        entries = json.loads(proc.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"ok": force, "reason": "preflight did not run", "blocking": []}
    blocking = [entry for entry in entries
                if entry.get("level") == "FAIL" and entry.get("name") in _BLOCKING]
    return {"ok": not blocking or force, "blocking": blocking,
            "entries": {str(entry.get("name")): str(entry.get("level"))
                        for entry in entries}}


def launch_all(wait_s: float = 60.0, confirm_s: float = 45.0,
               force: bool = False) -> Dict[str, Any]:
    """Click "Launch All (Nx)" and *verify* that instances actually started.

    Returns clicked/verified/attempts. A click that produced no change is retried once with a
    freshly read layout; it is never assumed to have worked.
    """
    gate = preflight(force=force)
    if not gate.get("ok"):
        return {"clicked": None, "verified": False, "refused": "preflight",
                "blocking": gate.get("blocking")}
    attempts: List[Dict[str, Any]] = []
    for attempt in range(3):
        focus_host()
        time.sleep(0.6)
        items = elements()
        windows = [item for item in items if str(item.get("type")) == "ControlType.Window"]
        host_window = windows[0] if windows else None
        target = None
        for item in items:
            if str(item.get("name") or "").lower().startswith("launch all"):
                target = item
                break
        if target is None:
            attempts.append({"attempt": attempt, "reason": "no Launch All control",
                             "items": len(items)})
            time.sleep(2.0)
            continue
        if not _safe_target(target, host_window):
            attempts.append({"attempt": attempt, "reason": "target rectangle looks unsafe",
                             "target": target, "window": host_window})
            time.sleep(2.0)
            continue
        before = running_counts(items)
        _click(int(target["x"]) + int(target["w"]) // 2,
               int(target["y"]) + int(target["h"]) // 2)
        deadline = time.time() + confirm_s
        while time.time() < deadline:
            time.sleep(3.0)
            dismiss_alerts(limit=3)
            counts = running_counts()
            if counts and before and counts["running"] > before["running"]:
                attempts.append({"attempt": attempt, "clicked": target.get("name"),
                                 "before": before, "after": counts})
                return {"clicked": target.get("name"), "verified": True,
                        "attempts": attempts}
            if counts and before is None and counts["running"] > 0:
                return {"clicked": target.get("name"), "verified": True,
                        "attempts": attempts}
        attempts.append({"attempt": attempt, "clicked": target.get("name"),
                         "before": before, "after": running_counts(), "verified": False})
    return {"clicked": None, "verified": False, "attempts": attempts}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="print the UIA tree")
    ap.add_argument("--launch-all", action="store_true", help="start every instance")
    ap.add_argument("--running", action="store_true", help="print the N / M Running line")
    ap.add_argument("--start-host", action="store_true", help="launch DofusFarm.exe if needed")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="launch even if the preflight says the path to Ankama is dead")
    args = ap.parse_args(argv)

    if args.start_host and not start_host():
        print("the host did not come up", file=sys.stderr)
        return 2
    if args.launch_all:
        result = launch_all(force=args.force)
        print(json.dumps(result, indent=1))
        return 0 if result.get("clicked") else 1
    if args.running:
        counts = running_counts()
        print(json.dumps(counts or {}, indent=1))
        return 0 if counts else 1
    items = elements()
    if not items:
        print("no host window: start DofusFarm.exe", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(items, indent=1))
    else:
        for item in items:
            if str(item.get("name") or "").strip():
                print("%-14s %s" % (str(item.get("type")).replace("ControlType.", ""),
                                    str(item.get("name"))[:90]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
