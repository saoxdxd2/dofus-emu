"""Project wire captures into a normalized action log, and join price rows.

Two sources, deliberately kept apart because they carry different things:

* `trajectories/play_*.jsonl` -- the page recorder's wire capture. It has the
  action stream: map changes, fight edges, protocol verbs in both directions,
  kamas/xp/level deltas. It has **no prices**. The recorder caps every message
  at 6000 characters (`scripts/mobile-disguise.js`, `_shrink`), and
  `ObjectAveragePricesMessage` puts `avgPrices` *after* the `ids` array, so the
  cut lands before the first price. `--check` measures that on the corpus
  instead of trusting this paragraph.
* `market.json` -- written by `python -m advisor.cli capture`. It is the only
  place a server-sent price is ever persisted in full, because it is read from
  the client's `__PriceCache` (which absorbs the message before truncation) and
  written by Python, not by the recorder.

Output is JSONL with a `record` discriminator:

    {"record": "action", ...}   one row per wire event, ordered by file and seq
    {"record": "market", ...}   one row per (gid, capture), newest capture wins

Usage:
    python -m tools.market_log --check
    python -m tools.market_log --out trajectories/normalized.jsonl
    python -m tools.market_log --captures build/advisor/market.json --only market
    python -m tools.market_log --out - --limit 50

`--only market` with no capture on disk emits zero rows and says so; it does not
invent prices and it does not silently succeed. Add `--require-market` to make
that a non-zero exit for a pipeline that depends on the join.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
# `python tools/x.py` puts tools/ on sys.path and the repo root nowhere, so the
# agent import below fails unless the root is inserted first.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.jsonio import utf8_console  # noqa: E402 - needs the path insert above

DEFAULT_PLAY_GLOB = str(REPO_ROOT / "trajectories" / "play_*.jsonl")
DEFAULT_OUT = REPO_ROOT / "trajectories" / "normalized.jsonl"

# Frames whose payload is the market. Kept in one place so the projection and
# the coverage check can never disagree about what counts as a price frame.
MARKET_FRAMES = (
    "ObjectAveragePricesMessage",
    "ExchangeBidHouseInListAddedMessage",
    "ExchangeBidHouseInListUpdatedMessage",
)

# Order matters: the first match wins, so `market` is tested before `inventory`
# (both start with `Object`) and `combat` before `session` (`GameFight` vs
# `GameContext`).
VERB_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("market", ("ExchangeBidHouse", "ObjectAveragePrices", "setShopDetails",
                "shopBuy", "megaShop", "ExchangeObject", "ExchangeStart",
                "ExchangeLeave", "ExchangeRequest", "ObjectAveragePrice")),
    ("movement", ("GameMapMovement", "ChangeMap", "CurrentMap", "MapInformations",
                  "MapComplementary", "MapFightCount", "MapRunningFight",
                  "MapObstacle", "MapChange")),
    ("combat", ("GameFight", "GameAction", "GameRolePlay", "Spell", "Fight",
                "Challenge", "Turn", "GameDataPlayFarmState")),
    ("dialog", ("Npc", "Dialog", "Quest", "Interactive", "LeaveDialog")),
    ("inventory", ("Object", "Inventory", "Item", "Storage", "Kamas")),
    ("social", ("Chat", "Friend", "Guild", "Party", "Alliance", "TaxCollector",
                "Recruitment", "Ignored", "Mood")),
    ("session", ("connecting", "open", "disconnect", "disconnecting",
                 "Identification", "ServersList", "Character", "GameContext",
                 "QueueStatus", "BasicPing", "BasicPong", "SequenceNumber",
                 "BasicTime", "BasicLatency", "ladders", "AccountCapabilities",
                 "Authentification", "Server", "HelloConnect")),
)

SKIP_KINDS = frozenset({"console", "probe", "hook_report", "visibility"})


# ---------------------------------------------------------------------------
# helpers


def classify(name: Optional[str]) -> str:
    """Bucket a protocol message name into a coarse verb."""
    if not name:
        return "other"
    for verb, needles in VERB_RULES:
        for needle in needles:
            if needle in name:
                return verb
    return "other"


def at_iso(at: Any) -> Optional[str]:
    """Epoch milliseconds -> UTC ISO-8601. The recorder stamps `at` in ms."""
    if not isinstance(at, (int, float)) or at <= 0:
        return None
    try:
        return datetime.fromtimestamp(at / 1000.0, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def iter_records(paths: Iterable[str]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield (basename, record) for every parseable line, skipping bad lines.

    A truncated final line is normal for a capture that was killed mid-write, so
    a bad line is skipped rather than aborting the whole file.
    """
    for path in paths:
        name = os.path.basename(path)
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield name, rec


def _payload(document: Any) -> Dict[str, Any]:
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


# ---------------------------------------------------------------------------
# action projection


def project_action(src: str, rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One wire record -> one normalized action row, or None if it is noise."""
    event = rec.get("event")
    if not isinstance(event, dict):
        return None
    kind = event.get("kind")
    if kind in SKIP_KINDS or kind is None:
        return None

    row: Dict[str, Any] = {
        "record": "action",
        "src": src,
        "session": rec.get("session"),
        "seq": rec.get("seq"),
        "at": rec.get("at"),
    }
    iso = at_iso(rec.get("at"))
    if iso:
        row["at_iso"] = iso

    if kind in ("send", "recv"):
        name = event.get("name")
        row.update(verb=classify(name), dir=("out" if kind == "send" else "in"),
                   msg=name)
        if event.get("via"):
            row["via"] = event["via"]
        return row

    if kind == "session_start":
        row.update(verb="session", msg="session_start")
        if event.get("label"):
            row["label"] = event["label"]
        if event.get("href"):
            row["href"] = event["href"]
        return row

    if kind == "connect":
        row.update(verb="session", msg="connect")
        args = event.get("args")
        # `connect(handlers, url)` -- the url is the only useful half.
        if isinstance(args, list) and len(args) > 1 and isinstance(args[1], str):
            row["target"] = args[1]
        return row

    if kind == "map_change":
        row.update(verb="move", msg="map_change", map=event.get("to"))
        if event.get("from") is not None:
            row["map_from"] = event["from"]
        if event.get("cell") is not None:
            row["cell"] = event["cell"]
        return row

    if kind in ("fight_start", "fight_end"):
        state = event.get("state") or {}
        row.update(verb="combat", msg=kind, map=state.get("map"),
                   fight=(kind == "fight_start"))
        for field in ("hp", "max", "xp", "level", "kamas"):
            if state.get(field) is not None:
                row[field] = state[field]
        return row

    if kind in ("state", "state_delta"):
        state = event.get("state") or {}
        row.update(verb="state", msg=kind, map=state.get("map"),
                   fight=bool(state.get("fight")))
        for field in ("hp", "max", "xp", "level", "kamas"):
            if state.get(field) is not None:
                row[field] = state[field]
        diff = event.get("diff")
        if isinstance(diff, dict) and diff:
            # Keep the destination value only: `from` is the previous row's `to`,
            # so storing both doubles the file for no extra information.
            row["delta"] = {key: value.get("to")
                            for key, value in diff.items()
                            if isinstance(value, dict)}
        return row

    if kind == "click":
        row.update(verb="input", msg="click")
        for field in ("tag", "id", "cell"):
            if event.get(field) is not None:
                row[field] = event[field]
        return row

    if kind == "key":
        row.update(verb="input", msg="key", key=event.get("key"))
        if event.get("code"):
            row["code"] = event["code"]
        return row

    return None


# ---------------------------------------------------------------------------
# market projection


def project_capture(src: str, document: Any) -> List[Dict[str, Any]]:
    """One `market.json` -> one market row per gid.

    Rows are self-contained: they repeat the capture timestamps so a row stays
    meaningful after being shuffled into a training set.
    """
    data = _payload(document)
    if not data:
        return []
    captured_at = data.get("captured_at")
    average_at = data.get("average_at")
    average_age = data.get("average_age_ms")
    listings = data.get("listings") if isinstance(data.get("listings"), dict) else {}

    rows: List[Dict[str, Any]] = []
    for pair in data.get("prices") or []:
        gid = price = None
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            gid, price = pair[0], pair[1]
        elif isinstance(pair, dict):
            gid, price = pair.get("gid"), pair.get("average", pair.get("price"))
        try:
            gid = int(gid)
            price = float(price)
        except (TypeError, ValueError):
            continue
        if gid <= 0 or price <= 0:
            continue
        row: Dict[str, Any] = {
            "record": "market",
            "src": src,
            "gid": gid,
            "average": price,
            "captured_at": captured_at,
            "average_at": average_at,
            "average_age_ms": average_age,
        }
        listing = listings.get(str(gid))
        if isinstance(listing, dict):
            row["listing"] = {key: listing.get(key)
                              for key in ("count", "min", "median", "max", "age_ms")
                              if listing.get(key) is not None}
        rows.append(row)
    return rows


def merge_market_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse to one row per gid, keeping the newest capture.

    `average_at` is when the server produced the table; `captured_at` is when we
    read it. Prefer the former so a re-read of a stale cache does not look fresh.
    """
    newest: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        gid = row["gid"]
        key = row.get("average_at") or row.get("captured_at") or 0
        held = newest.get(gid)
        if held is None:
            newest[gid] = row
            continue
        held_key = held.get("average_at") or held.get("captured_at") or 0
        if key >= held_key:
            newest[gid] = row
    return [newest[gid] for gid in sorted(newest)]


# ---------------------------------------------------------------------------
# coverage check


def _empty_frame_stats() -> Dict[str, Any]:
    return {
        "frames": 0,
        "truncated": 0,
        "previews_with_prices": 0,
        "parseable_previews": 0,
        "recoverable_prices": 0,
        "by_name": {},
    }


def _scan_frame(event: Dict[str, Any], stats: Dict[str, Any]) -> None:
    """Fold one event into the frame statistics, if it is a price frame."""
    name = event.get("name")
    if name not in MARKET_FRAMES:
        return
    stats["frames"] += 1
    stats["by_name"][name] = stats["by_name"].get(name, 0) + 1
    args = event.get("args")
    if not isinstance(args, dict):
        return
    if args.get("_truncated"):
        stats["truncated"] += 1
        preview = args.get("preview")
        if isinstance(preview, str):
            # `prices`/`avgPrices` follow `ids`, so a cut preview never has it.
            if '"prices"' in preview or '"avgPrices"' in preview:
                stats["previews_with_prices"] += 1
            try:
                json.loads(preview)
                stats["parseable_previews"] += 1
            except ValueError:
                pass
        return
    prices = args.get("avgPrices") or args.get("prices")
    if isinstance(prices, list):
        stats["recoverable_prices"] += len(prices)


def scan_market_frames(paths: Iterable[str]) -> Dict[str, Any]:
    """Measure how much of the price payload actually survived the recorder.

    This is the number that decides whether a market projection over play logs
    is possible at all, so it is computed from the data, not assumed.
    """
    stats = _empty_frame_stats()
    for _src, rec in iter_records(paths):
        event = rec.get("event")
        if isinstance(event, dict):
            _scan_frame(event, stats)
    return stats


def resolve_captures(explicit: Optional[List[str]]) -> List[str]:
    """Find `market.json` files. Explicit paths win; otherwise look in the two
    places the advisor actually writes to."""
    if explicit:
        return [path for path in explicit if os.path.exists(path)]
    candidates: List[str] = []
    candidates.extend(sorted(glob.glob(str(REPO_ROOT / "trajectories" / "**" / "market.json"),
                                       recursive=True)))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "DofusFarm", "market.json"))
    return [path for path in candidates if os.path.exists(path)]


def build_check(paths: List[str], captures: List[str]) -> Dict[str, Any]:
    verbs: Dict[str, int] = {}
    records = 0
    actions = 0
    frames = _empty_frame_stats()
    # One pass: the corpus is 235k lines, and a second walk only to count price
    # frames doubled the cost of every `--check`.
    for src, rec in iter_records(paths):
        records += 1
        row = project_action(src, rec)
        if row is not None:
            actions += 1
            verbs[row["verb"]] = verbs.get(row["verb"], 0) + 1
        event = rec.get("event")
        if isinstance(event, dict):
            _scan_frame(event, frames)

    price_rows = 0
    for path in captures:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            continue
        price_rows += len(project_capture(os.path.basename(path), document))

    return {
        "files": len(paths),
        "records": records,
        "actions": actions,
        "verbs": dict(sorted(verbs.items(), key=lambda kv: -kv[1])),
        "market_frames": frames,
        "captures": len(captures),
        "market_rows": price_rows,
        "prices_from_play_logs": frames["recoverable_prices"],
    }


def render_check(report: Dict[str, Any]) -> List[str]:
    frames = report["market_frames"]
    lines = [
        "files              %d" % report["files"],
        "records            %d" % report["records"],
        "action rows        %d" % report["actions"],
        "verbs              %s" % ", ".join("%s=%d" % kv for kv in report["verbs"].items()),
        "",
        "price frames       %d (%s)"
        % (frames["frames"],
           ", ".join("%s=%d" % kv for kv in sorted(frames["by_name"].items())) or "none"),
        "  truncated        %d" % frames["truncated"],
        "  parseable        %d of %d previews"
        % (frames["parseable_previews"], frames["truncated"]),
        "  prices surviving %d" % frames["recoverable_prices"],
        "",
        "captures on disk   %d" % report["captures"],
        "market rows        %d" % report["market_rows"],
    ]
    if frames["frames"] and not frames["recoverable_prices"]:
        lines.append("")
        lines.append(
            "Play logs carry no usable prices: the recorder cuts every message at "
            "6000 chars and `avgPrices` follows `ids`, so the cut lands before the "
            "first price. That is a property of the capture, not of this tool.")
        lines.append(
            "Prices must come from a market capture, which reads the client's "
            "untruncated price cache:")
        lines.append("    python -m advisor.cli capture --out build/advisor")
    if not report["captures"]:
        lines.append("")
        lines.append("No market.json found; `--only market` would emit 0 rows.")
    return lines


# ---------------------------------------------------------------------------
# main


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project play_*.jsonl into a normalized action log, "
                    "joined with price rows from market.json.")
    parser.add_argument("paths", nargs="*",
                        help="play_*.jsonl files (default: trajectories/play_*.jsonl)")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="output JSONL, or - for stdout (default: %(default)s)")
    parser.add_argument("--captures", nargs="*", default=None,
                        help="market.json files (default: auto-discover)")
    parser.add_argument("--only", choices=("action", "market", "both"), default="both")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N rows (0 = no limit)")
    parser.add_argument("--check", action="store_true",
                        help="report coverage and exit; write nothing")
    parser.add_argument("--json", action="store_true",
                        help="with --check, print the report as JSON")
    parser.add_argument("--require-market", action="store_true",
                        help="exit 1 when no price row can be produced")
    args = parser.parse_args(argv)

    paths = args.paths or sorted(glob.glob(DEFAULT_PLAY_GLOB))
    if not paths:
        print("no play_*.jsonl found; pass paths explicitly", file=sys.stderr)
        return 2
    captures = resolve_captures(args.captures)

    if args.check:
        report = build_check(paths, captures)
        if args.json:
            # Item names reach this JSON through the corpus, so the console has
            # to be UTF-8 before anything is printed.
            utf8_console()
            print(json.dumps(report, ensure_ascii=False, indent=1))
        else:
            print("\n".join(render_check(report)))
        if args.require_market and report["market_rows"] == 0:
            return 1
        return 0

    market_rows: List[Dict[str, Any]] = []
    if args.only in ("market", "both"):
        collected: List[Dict[str, Any]] = []
        for path in captures:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    document = json.load(handle)
            except (OSError, ValueError) as exc:
                print("skipping %s: %s" % (path, exc), file=sys.stderr)
                continue
            collected.extend(project_capture(os.path.basename(path), document))
        market_rows = merge_market_rows(collected)
        if not market_rows:
            print("no price rows: %d capture(s) on disk. Run "
                  "`python -m advisor.cli capture --out build/advisor` to make one."
                  % len(captures), file=sys.stderr)

    def action_rows() -> Iterator[Dict[str, Any]]:
        if args.only == "market":
            return
        for src, rec in iter_records(paths):
            row = project_action(src, rec)
            if row is not None:
                yield row

    def combined() -> Iterator[Dict[str, Any]]:
        # Actions first, in file order, then the joined price rows: a reader
        # that wants the action stream can stop before the market block.
        for row in action_rows():
            yield row
        if args.only in ("market", "both"):
            for row in market_rows:
                yield row

    stream = args.out == "-"
    handle = sys.stdout if stream else open(args.out, "w", encoding="utf-8")
    written = 0
    try:
        for row in combined():
            if args.limit and written >= args.limit:
                break
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            written += 1
    finally:
        if not stream:
            handle.close()

    if not stream:
        print("wrote %d row(s) -> %s" % (written, args.out))
    if args.require_market and not market_rows:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
