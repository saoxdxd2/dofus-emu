"""Sample public Dofusbook builds without scraping the whole site.

The public Touch API is same-origin under ``/api``. This sampler:

* reads the class catalogue and site counts;
* tries the public build search (the endpoint can be flaky; falls back to the
  latest public builds);
* fetches only a small, rate-limited number of build details;
* saves combat-relevant fields (class, level, tags, computed stats, item ids,
  forge-magie bonuses and scroll/base characteristics) and drops user/profile
  text.

No login, no credentials, no gating bypass. Keep the request rate low and the
sample small; this is a fan site, not a bulk data source.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
NORMAL_CAPS = {"ap": 12, "mp": 6, "range": 6}
DEFAULT_OUT = REPO_ROOT / "data" / "cache" / "dofusbook_builds.jsonl"
DEFAULT_SUMMARY = REPO_ROOT / "data" / "cache" / "dofusbook_builds_summary.json"
BASE = "https://touch.dofusbook.net/api"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/150.0 Safari/537.36",
    "Referer": "https://touch.dofusbook.net/mobile/fr/equipement/stuffs-publics",
}
KEEP_BUILD = (
    "id", "character_class", "character_level", "allowed_classes", "tags",
    "stuffItem", "stuffFmItem", "stuffCarac", "stuffFm",
)


def get_json(path: str, params: Optional[Dict[str, Any]] = None,
             retries: int = 3, timeout: int = 25) -> Any:
    url = BASE + "/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        request = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            last_error = RuntimeError(f"HTTP {exc.code} {url}: {body}")
            if exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(last_error or "request failed"))


def public_hits(page: int) -> List[Dict[str, Any]]:
    payload = get_json("search/touch/stuffs",
                       {"page": page, "sort": "update-desc"})
    hits = payload.get("hits") or []
    return [hit for hit in hits if isinstance(hit, dict)]


def latest_hits() -> List[Dict[str, Any]]:
    payload = get_json("stuffs/touch/public/latest")
    return [row for row in payload.get("rows") or [] if isinstance(row, dict)]


def build_detail(identifier: Any) -> Optional[Dict[str, Any]]:
    try:
        payload = get_json(f"stuffs/touch/public/{identifier}")
    except RuntimeError:
        return None
    stuff = payload.get("stuff")
    return stuff if isinstance(stuff, dict) else None


def compact(summary: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**summary, **detail}
    out = {key: merged.get(key) for key in KEEP_BUILD if key in merged}
    out["id"] = merged.get("id")
    out["class_id"] = merged.get("character_class")
    out["level"] = merged.get("character_level")
    out["tags"] = merged.get("tags") or []
    summary_stat = summary.get("stat") if isinstance(summary.get("stat"), dict) else {}
    detail_stat = detail.get("stat") if isinstance(detail.get("stat"), dict) else {}
    combat_keys = ("pa", "pm", "vi", "fo", "in", "ag", "ch", "po", "cc", "dp")
    if any(key in summary_stat for key in combat_keys):
        out["stat"] = summary_stat
    if detail_stat.get("views") is not None:
        out["views"] = detail_stat["views"]
    return out


def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    classes = {}
    stats = {}
    try:
        classes = get_json("bootstrap")
    except RuntimeError as exc:
        print(f"bootstrap unavailable: {exc}")
    try:
        stats = get_json("stats")
    except RuntimeError as exc:
        print(f"stats unavailable: {exc}")

    hits: List[Dict[str, Any]] = []
    search_error = None
    for page in range(1, max(1, args.pages) + 1):
        try:
            hits = public_hits(page)
            break
        except RuntimeError as exc:
            search_error = str(exc)
            break
    if not hits:
        print(f"public search unavailable ({search_error}); using latest builds")
        try:
            hits = latest_hits()
        except RuntimeError as exc:
            raise SystemExit(f"cannot list public builds: {exc}")

    written = 0
    seen = set()
    with out.open("w", encoding="utf-8") as handle:
        for hit in hits:
            if written >= args.max_builds:
                break
            identifier = hit.get("id") or hit.get("slug")
            if identifier is None or identifier in seen:
                continue
            seen.add(identifier)
            detail = build_detail(identifier)
            if not detail:
                continue
            handle.write(json.dumps(compact(hit, detail), ensure_ascii=False) + "\n")
            written += 1
            print(f"saved build {identifier} ({written}/{args.max_builds})")
            time.sleep(max(0.5, args.delay))
    summary = {
        "source": "dofusbook_public_api",
        "search_error": search_error,
        "builds_saved": written,
        "classes": classes,
        "site_stats": stats,
        "note": ("Public builds only; profile text and user fields are dropped. "
                 "Normal Touch caps are 12 AP / 6 MP / 6 range; Temporix/event "
                 "builds above those caps must not be used as normal-game builds."),
        "out": str(out),
    }
    Path(args.summary).write_text(json.dumps(summary, indent=1),
                                  encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("classes", "site_stats")}, indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pages", type=int, default=1,
                   help="public search pages to try (20 builds per page)")
    p.add_argument("--max-builds", type=int, default=20)
    p.add_argument("--delay", type=float, default=1.2,
                   help="seconds between build-detail requests")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    return p


def main(argv: Optional[List[str]] = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
