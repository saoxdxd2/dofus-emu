"""Minimal Kaggle MCP client — stdlib only, no SDK.

Why this exists
---------------
Kaggle exposes an official MCP server at ``https://www.kaggle.com/mcp``
(Streamable HTTP, protocol 2025-06-18, 76 tools). The desktop client needs the
connector to be trusted in the UI before its tools appear; this script talks to
the same endpoint directly so training runs are not blocked on that step.

Token resolution, in order:
    1. ``KAGGLE_API_TOKEN`` environment variable
    2. ``~/.kaggle/access_token``   (the Kaggle CLI 2.x file)
    3. ``key`` from ``~/.kaggle/kaggle.json``

Never pass the token on the command line — it ends up in shell history.

Usage::

    python tools/kaggle_mcp.py list                       # tool names
    python tools/kaggle_mcp.py list --filter notebook
    python tools/kaggle_mcp.py schema get_accelerator_quota
    python tools/kaggle_mcp.py quota                      # GPU/TPU usage
    python tools/kaggle_mcp.py call get_dataset_info '{"owner":"tajdinenizar","dataset":"thx01-encoder-core"}'
    python tools/kaggle_mcp.py call search_datasets '{"search":"dofus"}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

URL = "https://www.kaggle.com/mcp"
PROTOCOL = "2025-06-18"


def resolve_token() -> str:
    token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if token:
        return token
    home = Path.home() / ".kaggle"
    for name in ("access_token",):
        path = home / name
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    legacy = home / "kaggle.json"
    if legacy.exists():
        try:
            payload = json.loads(legacy.read_text(encoding="utf-8"))
            if payload.get("key"):
                return str(payload["key"]).strip()
        except json.JSONDecodeError:
            pass
    raise SystemExit("no Kaggle token: set KAGGLE_API_TOKEN or ~/.kaggle/access_token")


class KaggleMCP:
    """One JSON-RPC connection. Stateless on the server side, so no session id."""

    def __init__(self, token: Optional[str] = None, timeout: int = 120) -> None:
        self.token = token or resolve_token()
        self.timeout = timeout
        self.session: Optional[str] = None
        self._next_id = 0
        self._initialized = False

    # -- plumbing ------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        return headers

    @staticmethod
    def _parse(raw: str) -> Optional[Dict[str, Any]]:
        """Accept either a JSON body or an SSE stream (``data: {...}``)."""
        text = (raw or "").strip()
        if not text:
            return None
        if text.startswith("{"):
            return json.loads(text)
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise ValueError(f"unparseable MCP response: {text[:200]!r}")

    def _post(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        request = urllib.request.Request(
            URL, data=json.dumps(payload).encode("utf-8"), headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session = session
                return self._parse(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {exc.code} from Kaggle MCP: {body[:400]}") from exc

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method,
                   "params": params or {}}
        response = self._post(payload) or {}
        if "error" in response:
            raise RuntimeError(f"{method} failed: {response['error']}")
        return response.get("result")

    # -- protocol ------------------------------------------------------------

    def initialize(self) -> Dict[str, Any]:
        if self._initialized:
            return {}
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "dofus-agent", "version": "1.0"},
        }) or {}
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True
        return result

    def tools(self) -> List[Dict[str, Any]]:
        self.initialize()
        result = self._rpc("tools/list") or {}
        return result.get("tools") or []

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Invoke a tool. Returns the parsed payload (JSON if possible)."""
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result is None:
            return None
        content = result.get("content") or []
        chunks: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                chunks.append(str(part.get("text") or ""))
        text = "\n".join(chunks)
        if result.get("isError"):
            raise RuntimeError(f"{name} returned an error: {text[:600]}")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="list tools")
    list_parser.add_argument("--filter", default="")
    list_parser.add_argument("--full", action="store_true")

    schema_parser = sub.add_parser("schema", help="show one tool's input schema")
    schema_parser.add_argument("tool")

    call_parser = sub.add_parser("call", help="call a tool with JSON arguments")
    call_parser.add_argument("tool")
    call_parser.add_argument("arguments", nargs="?", default="{}")

    sub.add_parser("quota", help="show accelerator quota")

    args = parser.parse_args(argv)
    client = KaggleMCP()

    if args.command == "list":
        tools = client.tools()
        needle = args.filter.lower()
        shown = 0
        for tool in tools:
            name = tool.get("name", "")
            if needle and needle not in name.lower():
                continue
            shown += 1
            if args.full:
                print(json.dumps(tool, indent=2)[:1200])
            else:
                description = (tool.get("description") or "").split("\n")[0][:90]
                print(f"{name:44s} {description}")
        print(f"\n{shown}/{len(tools)} tools", file=sys.stderr)
        return 0

    if args.command == "schema":
        for tool in client.tools():
            if tool.get("name") == args.tool:
                print(json.dumps(tool.get("inputSchema") or {}, indent=2))
                return 0
        print(f"no tool named {args.tool!r}", file=sys.stderr)
        return 1

    if args.command == "call":
        try:
            arguments = json.loads(args.arguments)
        except json.JSONDecodeError as exc:
            print(f"arguments must be JSON: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(client.call(args.tool, arguments), indent=2, ensure_ascii=False)[:8000])
        return 0

    if args.command == "quota":
        print(json.dumps(client.call("get_accelerator_quota", {}), indent=2, ensure_ascii=False))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
