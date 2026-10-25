"""Advisor CLI: capture through the local bridge, report offline.

    python -m advisor capture --out advisor_runs/latest
    python -m advisor report --character advisor_runs/latest/character.json \
        --market advisor_runs/latest/market.json
    python -m advisor selftest

Capture is read-only. It asks the client for `debug.character` and
`market.capture`, both of which only read the loaded game state and the price
cache. It never signs in, never spends points, never buys.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.bridge import directive_url, opener_for  # noqa: E402

from .models import CharacterSnapshot, MarketSnapshot  # noqa: E402
from .report import build_report  # noqa: E402
from .repository import Repository  # noqa: E402

MAX_PAGE = 20000


def _post(payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    url = directive_url()
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"},
        method="POST")
    try:
        # Local opener: HTTP_PROXY must not intercept loopback.
        with opener_for(url).open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("bridge answered HTTP %s at %s" % (exc.code, url)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(
            "bridge is not answering at %s (%s). Start the client and check "
            "%%LOCALAPPDATA%%\\DofusFarm\\bridge_port.txt." % (url, exc)) from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("bridge returned non-JSON at %s" % url) from exc


def _client_data(response: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap `{ok, result:{ok, data}}` and surface the game's own error."""
    if not isinstance(response, dict):
        raise RuntimeError("unexpected bridge response type %r" % type(response).__name__)
    inner = response.get("result")
    if isinstance(inner, dict):
        if inner.get("ok") is False:
            raise RuntimeError("client rejected the directive: %s"
                               % (inner.get("error") or "unknown error"))
        data = inner.get("data")
        return data if isinstance(data, dict) else {}
    if response.get("ok") is False:
        raise RuntimeError("bridge rejected the directive: %s"
                           % (response.get("error") or "unknown error"))
    data = response.get("data")
    return data if isinstance(data, dict) else response


def run_directive(
    tool: str,
    directive: str,
    args: Dict[str, Any],
    timeout: float = 30.0,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "id": uuid.uuid4().hex,
        "ts": round(time.time(), 3),
        "directive": directive,
        "tool": tool,
        "args": args,
        "source": "advisor",
    }
    if instance and instance != "default":
        payload["target_instance"] = instance
    return _client_data(_post(payload, timeout))


def capture_character(
    instance: Optional[str] = None,
    include_item_spells: bool = False,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    data = run_directive(
        "debug.character", "CHARACTER_REPORT",
        {
            "spell_grades": True,
            "spell_effects": False,
            "include_item_spells": bool(include_item_spells),
        },
        timeout=timeout, instance=instance)
    if not data.get("character"):
        raise RuntimeError(
            "the character report came back empty; the client is probably not "
            "in world at character selection")
    return data


def _merge_prices(target: Dict[int, float], rows: Any) -> int:
    count = 0
    for pair in rows or []:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        try:
            gid = int(pair[0])
            price = float(pair[1])
        except (TypeError, ValueError):
            continue
        if gid > 0 and price > 0:
            target[gid] = price
            count += 1
    return count


def capture_market(
    refresh: bool = True,
    wait: float = 6.0,
    pages: int = 20,
    instance: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Page through the whole average-price table the server sent.

    `ObjectAveragePricesMessage` is asynchronous: `refresh` asks the client to
    send the request, and the numbers only enter the page cache when the answer
    lands. One short wait is enough; every later page is a pure cache read.
    """
    call = {
        "refresh": bool(refresh),
        "offset": 0,
        "limit": MAX_PAGE,
        "listing_limit": 500,
    }
    first = run_directive("market.capture", "MARKET_CAPTURE", call,
                          timeout=timeout, instance=instance)
    requested = bool(first.get("requested"))
    request_error = first.get("request_error")
    if requested and wait > 0:
        time.sleep(wait)
        call["refresh"] = False
        first = run_directive("market.capture", "MARKET_CAPTURE", call,
                              timeout=timeout, instance=instance)

    prices: Dict[int, float] = {}
    listings: Dict[str, Any] = dict(first.get("listings") or {})
    _merge_prices(prices, first.get("prices"))
    total = int(first.get("average_count") or len(prices))
    offset = len(first.get("prices") or [])
    page = 1
    while offset < total and page < max(1, pages):
        data = run_directive(
            "market.capture", "MARKET_CAPTURE",
            {"refresh": False, "offset": offset, "limit": MAX_PAGE,
             "listing_limit": 0},
            timeout=timeout, instance=instance)
        rows = data.get("prices") or []
        added = _merge_prices(prices, rows)
        listings.update(data.get("listings") or {})
        total = max(total, int(data.get("average_count") or 0))
        if added <= 0:
            break
        offset += len(rows)
        page += 1

    now_ms = int(time.time() * 1000)
    average_at = first.get("average_at")
    average_age = (now_ms - int(average_at)) if average_at else None
    return {
        "captured_at": now_ms,
        "average_at": average_at,
        "average_age_ms": average_age,
        "average_count": len(prices),
        "listing_count": len(listings),
        "requested": requested,
        "request_error": request_error,
        "prices": [[gid, prices[gid]] for gid in sorted(prices)],
        "listings": listings,
        "pages": page,
    }


# ---------------------------------------------------------------------------
# report loading


def _payload_data(document: Any) -> Dict[str, Any]:
    """Accept a saved capture or a full bridge envelope."""
    if not isinstance(document, dict):
        return {}
    result = document.get("result")
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        return result["data"]
    data = document.get("data")
    if isinstance(data, dict):
        return data
    return document


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_character(path: Path) -> CharacterSnapshot:
    return CharacterSnapshot.from_bridge(_payload_data(load_json(path)))


def load_market(path: Optional[Path]) -> MarketSnapshot:
    if path is None:
        return MarketSnapshot()
    return MarketSnapshot.from_bridge(_payload_data(load_json(path)))


def _write_json(path: Path, data: Any, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, ensure_ascii=False, indent=1)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# commands


def _cmd_capture(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    character = capture_character(
        instance=args.instance, include_item_spells=args.item_spells,
        timeout=args.timeout)
    _write_json(out / "character.json", character)
    snapshot = CharacterSnapshot.from_bridge(character)
    print("[advisor] character: level %s, %d class spell(s), %d equipped -> %s"
          % (snapshot.level,
             len([spell for spell in snapshot.spells if not spell.is_item]),
             len(snapshot.equipment), out / "character.json"))

    if args.no_market:
        return 0
    market = capture_market(
        refresh=not args.no_refresh, wait=args.wait, pages=args.pages,
        instance=args.instance, timeout=args.timeout)
    _write_json(out / "market.json", market, compact=True)
    print("[advisor] market: %d average(s), %d listing(s), %d page(s) -> %s"
          % (market["average_count"], market["listing_count"], market["pages"],
             out / "market.json"))
    if market.get("request_error"):
        print("[advisor] average-price request was refused: %s"
              % market["request_error"])
    elif market.get("requested") and market["average_count"] == 0 and args.wait > 0:
        print("[advisor] the average table is still empty after %.1fs; the "
              "server answer may take longer. Re-run capture without --refresh."
              % args.wait)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    character_path = Path(args.character).resolve()
    if not character_path.exists():
        print("[advisor] no character capture at %s" % character_path, file=sys.stderr)
        return 2
    market_path = Path(args.market).resolve() if args.market else None
    if market_path is not None and not market_path.exists():
        print("[advisor] no market capture at %s" % market_path, file=sys.stderr)
        return 2
    repo = Repository()
    character = load_character(character_path)
    market = load_market(market_path)
    report = build_report(
        repo, character, market, focus=args.focus, budget=args.budget,
        level_margin=args.level_margin, min_price=args.min_price,
        min_discount=args.min_discount)
    if args.summary_only:
        print("\n".join(report.summary()))
    else:
        print(report.render_text())
    if args.json:
        data = report.to_dict()
        if args.json == "-":
            print(json.dumps(data, ensure_ascii=False, indent=1))
        else:
            path = Path(args.json).resolve()
            _write_json(path, data)
            print("\n[advisor] wrote %s" % path)
    return 0


def _cmd_selftest(_args: argparse.Namespace) -> int:
    from .selftest import run

    failures = run()
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="advisor",
        description="Read-only spell, stat, gear and market advisor for Dofus Touch.")
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="read the live character and price cache")
    capture.add_argument("--out", default="advisor_runs/latest",
                         help="directory for character.json and market.json")
    capture.add_argument("--instance", default=None,
                         help="bridge target instance, when several clients run")
    capture.add_argument("--item-spells", action="store_true",
                         help="include item-granted spells (not allocatable)")
    capture.add_argument("--no-market", action="store_true",
                         help="capture only the character report")
    capture.add_argument("--no-refresh", action="store_true",
                         help="do not ask the client to re-request average prices")
    capture.add_argument("--wait", type=float, default=6.0,
                         help="seconds to wait for the asynchronous average table")
    capture.add_argument("--pages", type=int, default=20,
                         help="maximum 20k-row pages of average prices")
    capture.add_argument("--timeout", type=float, default=30.0,
                         help="HTTP timeout per bridge call")
    capture.set_defaults(func=_cmd_capture)

    report = sub.add_parser("report", help="build a report from saved captures")
    report.add_argument("--character", required=True, help="saved character.json")
    report.add_argument("--market", default=None, help="saved market.json")
    report.add_argument("--focus", default="auto",
                        help="auto | existing | earth | fire | water | air")
    report.add_argument("--budget", type=int, default=None,
                        help="override the spell-point budget")
    report.add_argument("--level-margin", type=int, default=5,
                        help="how far above the character level gear may be")
    report.add_argument("--min-price", type=float, default=100.0,
                        help="ignore buy signals below this average price")
    report.add_argument("--min-discount", type=float, default=0.15,
                        help="minimum discount below average for a buy signal")
    report.add_argument("--summary-only", action="store_true",
                        help="print only the top summary lines")
    report.add_argument("--json", default=None,
                        help="write the full report as JSON ('-' for stdout)")
    report.set_defaults(func=_cmd_report)

    selftest = sub.add_parser("selftest", help="run the offline advisor checks")
    selftest.set_defaults(func=_cmd_selftest)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RuntimeError as exc:
        print("[advisor] %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
