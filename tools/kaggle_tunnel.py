"""Run the Kaggle CLI with SOCKS remote DNS and a process-local direct-network veto."""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import runpy
import socket
import sys


def network_veto(port):
    def audit(event, args):
        if event == "socket.connect":
            sock, address = args
            if (sock.family != socket.AF_INET or sock.type != socket.SOCK_STREAM
                    or address != ("127.0.0.1", port)):
                raise PermissionError("direct network connection refused")
        elif event == "socket.getaddrinfo":
            if args[0] != "127.0.0.1":
                raise PermissionError("local DNS lookup refused")
        elif event in ("socket.gethostbyname", "socket.gethostbyaddr",
                       "socket.getnameinfo", "socket.sendto", "socket.sendmsg",
                       "subprocess.Popen", "os.system", "os.posix_spawn"):
            raise PermissionError("network bypass operation refused")
    return audit


def protect(port):
    import socks
    import requests

    proxy = "socks5h://127.0.0.1:%d" % port
    for key in list(os.environ):
        if key.lower().endswith("_proxy"):
            del os.environ[key]
    os.environ.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy, NO_PROXY="")
    original_send = requests.Session.send

    def send(session, request, **kwargs):
        kwargs["proxies"] = {"http": proxy, "https": proxy}
        kwargs.setdefault("timeout", 60)
        return original_send(session, request, **kwargs)

    requests.Session.send = send
    sys.addaudithook(network_veto(port))


def scrub(text: str) -> str:
    text = re.sub(r"(?i)(access_token|api_key|authorization|bearer)(\s*[:=]\s*)[^\s]+", r"\1\2[redacted]", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[address omitted]", text)
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[query omitted]", text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy-port", type=int, default=40000)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("supply Kaggle CLI arguments after --")
    protect(args.proxy_port)
    output = io.StringIO()
    code = 0
    failure = None
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        try:
            sys.argv = ["kaggle", *command]
            runpy.run_module("kaggle", run_name="__main__")
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else int(exc.code is not None)
        except Exception as exc:                                   # noqa: BLE001 - safe redaction
            code = 1
            status = getattr(getattr(exc, "response", None), "status_code", None)
            failure = "%s%s" % (type(exc).__name__, "" if status is None else "(HTTP %d)" % status)
    if code:
        import tempfile
        safe = scrub(output.getvalue())
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False,
                                              prefix="kaggle_tunnel_failure_", suffix=".txt")
        handle.write(safe)
        handle.close()
        print("Kaggle command failed through SOCKS (%s); scrubbed diagnostics: %s; no direct retry."
              % (failure or "CLI exit", handle.name))
    else:
        text = scrub(output.getvalue())
        print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
