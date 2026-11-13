"""Does the guest grant complete in a real browser, from this machine's egress?

G121 proved the popup gets a CloudFront AWS WAF challenge (`202` +
`x-amzn-waf-action: challenge`, a 1,999-byte white page) instead of the redirect
to `dofustouch://authorized?code=...`, and that `DofusFarm.log` has never seen
that redirect. Two questions decide what to build next:

  1. Can the challenge be solved at all from here, or is the egress itself the
     problem? The system browser is Edge -- the same engine as WebView2 -- so a
     pass here means the difference is the profile, not the network.
  2. Does the callback path work end to end (`dofustouch://` -> a running app)?

The probe does not disturb the app: it temporarily points the per-user
`dofustouch` handler at a recorder, opens the real authorize URL in the default
browser, and watches for the redirect. The handler is restored from a `finally`,
and the restore is verified by re-reading the key.

    python scratch/prove_guest_browser.py

Exit 0 means the browser was redirected to `dofustouch://authorized` with a code.
"""

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
import winreg
from pathlib import Path

KEY = r"Software\Classes\dofustouch\shell\open\command"
STATE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm"
RECORD = STATE / "deeplink-probe.txt"
CLIENT_ID = "18"
REDIRECT = "dofustouch://authorized"
AUTHORIZE = "https://auth.ankama.com/login/ankama-guest"
TIMEOUT_S = 90


def guest_token() -> str:
    req = urllib.request.Request(
        "https://auth.ankama.com/token/create-guest",
        data=b"client_id=" + CLIENT_ID.encode("ascii"), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["token"]


def pkce() -> str:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def read_handler() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return ""


def write_handler(command: str) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)


def main() -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        if RECORD.exists():
            RECORD.unlink()
    except OSError:
        pass

    print("[1] minting a guest token")
    token = guest_token()
    url = AUTHORIZE + "?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "code_challenge": pkce(),
        "redirect_uri": REDIRECT,
        "origin_tracker": "https://www.dofus-touch.com/",
        "token": token,
    })
    print("    token=%s... url=%s?...(%d chars)"
          % (token[:12], AUTHORIZE, len(url)))

    original = read_handler()
    print("[2] handler before: %s" % (original or "(none)"))
    # A .cmd shim, not `cmd /c echo %1`: the URL carries `&`, which cmd would read
    # as a command separator, and `%~1` strips the quotes the registry adds.
    shim = STATE / "deeplink-probe.cmd"
    shim.write_text("@echo off\r\n>>\"" + str(RECORD) + "\" echo %~1\r\n", encoding="ascii")
    recorder = "\"" + str(shim) + "\" \"%1\""
    installed = False
    try:
        write_handler(recorder)
        installed = True
        print("[3] handler now records the redirect; opening the URL in the default browser")
        opened = webbrowser.open(url, new=1, autoraise=True)
        print("    webbrowser.open -> %r" % (opened,))

        print("[4] waiting up to %ds for dofustouch://authorized" % TIMEOUT_S)
        deadline = time.time() + TIMEOUT_S
        seen = ""
        while time.time() < deadline:
            if RECORD.exists():
                try:
                    seen = RECORD.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    seen = ""
                if seen:
                    break
            time.sleep(1.0)
    finally:
        if installed:
            write_handler(original)
            restored = read_handler()
            print("[5] handler restored: %s" % ("yes" if restored == original else "NO -> " + restored))
            for leftover in (RECORD, shim):
                try:
                    if leftover.exists():
                        leftover.unlink()
                except OSError:
                    pass

    if not seen:
        print("RESULT: no redirect in %ds. The challenge was not solved by the system" % TIMEOUT_S)
        print("        browser either, so the block is the egress/profile, not WebView2.")
        return 1

    print("RESULT: the browser reached %s" % seen.split("?")[0])
    query = urllib.parse.parse_qs(urllib.parse.urlparse(seen).query)
    print("        code=%s state=%s error=%s"
          % ((query.get("code") or [""])[0][:10] or "-",
             (query.get("state") or [""])[0][:10] or "-",
             (query.get("error") or [""])[0] or "-"))
    ok = bool(query.get("code")) and not query.get("error")
    if not ok:
        print("        the redirect arrived without a usable code")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
