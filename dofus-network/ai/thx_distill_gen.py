"""Generate clean core-skill distillation data with the Orca/DeepSeek free teacher.

The teacher only labels *intent* (tool + typed args).  Every item is validated
against ``agent.schema`` and rejected if it mentions a known game entity, so the
dataset cannot teach the student memorised item/NPC/monster facts - only the
decision skill.

Usage (from ``dofus-network``)::

    python ai/thx_distill_gen.py --per-task 16 --workers 4
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.schema import TOOLS_BY_NAME, validate_call  # noqa: E402
from ai.thx_policy import _find_any_entity, _normalize  # noqa: E402

ORCA_URL = "https://api.orcarouter.ai/v1/chat/completions"
ORCA_KEY = "sk-orca-R0NUpTHxxmqZj46cBgBmdyZ7Qg2bgHChR5WRkUGMOvj"
ORCA_MODEL = "deepseek/deepseek-v4.1-flash-free"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
GEMINI_KEY = "AQ.Ab8RN6Ifh1nrh3KyuJkTuKUC_FW6rvMipV1Bg6nieB_lDX1h-w"
GEMINI_MODEL = "gemini-3.8-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY = "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"
OPENROUTER_MODEL = "minimax/minimax-m3.1-flash:free"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_KEY = "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"
NVIDIA_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"

_PROVIDER = {"name": "gemini", "model": None}

LANG_NAMES = {"en": "English", "fr": "French", "es": "Spanish", "de": "German"}
LANGS = tuple(LANG_NAMES)
TOOLS = tuple(TOOLS_BY_NAME)


def _tool_schema(tool: str) -> Dict[str, Any]:
    spec = TOOLS_BY_NAME[tool]
    params = {}
    for name, param in spec.params.items():
        item: Dict[str, Any] = {"type": param.get("type", "str")}
        if param.get("required"):
            item["required"] = True
        if param.get("enum"):
            item["enum"] = list(param["enum"])
        params[name] = item
    return {
        "tool": tool,
        "description": spec.description,
        "args": params,
    }


def _prompt(tool: str, lang: str, count: int) -> str:
    schema = _tool_schema(tool)
    return (
        "You generate synthetic supervised training data for a Dofus Touch assistant.\n"
        f"Target tool: {tool}\n"
        f"Tool description: {schema['description']}\n"
        f"Argument schema: {json.dumps(schema['args'], ensure_ascii=False)}\n"
        f"Generate {count} diverse, short user commands written in {LANG_NAMES[lang]} "
        f"whose intent is exactly the tool `{tool}`.\n"
        "Hard rules:\n"
        "- Use invented fantasy entity names. NEVER use a real Dofus monster, item, NPC, "
        "quest, spell, area or dungeon name.\n"
        "- Vary sentence structure, verbs and politeness markers.\n"
        "- Fill `args` exactly as the argument schema requires; use integer numbers for "
        "quantity/price/quest_id/coordinates and string names for entity arguments.\n"
        "- Output ONLY a JSON array, no markdown fences and no commentary:\n"
        f"[{{\"goal\": \"...\", \"tool\": \"{tool}\", \"args\": {{...}}}}, ...]\n"
    )


def _extract_gemini(payload: Dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(part.get("text") or "" for part in parts)


def _post(url: str, headers: Dict[str, str], body: Dict[str, Any],
          timeout: int = 240) -> Tuple[int, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def _call(prompt: str, retries: int = 6) -> Optional[str]:
    provider = _PROVIDER["name"]
    model = _PROVIDER["model"]
    for attempt in range(retries):
        if provider == "gemini":
            status, text = _post(
                GEMINI_URL.format(model=model or GEMINI_MODEL, key=GEMINI_KEY),
                {},
                {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": 16384,
                        "temperature": 0.95,
                    },
                },
            )
            if status == 200:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = {}
                content = _extract_gemini(payload)
                if content.strip():
                    return content
        elif provider == "openrouter":
            status, text = _post(
                OPENROUTER_URL,
                {"Authorization": f"Bearer {OPENROUTER_KEY}"},
                {
                    "model": model or OPENROUTER_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 8000,
                    "temperature": 0.95,
                },
            )
            if status == 200:
                try:
                    payload = json.loads(text)
                    content = (payload["choices"][0].get("message") or {}).get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    content = ""
                if content.strip():
                    return content
        elif provider == "nvidia":
            status, text = _post(
                NVIDIA_URL,
                {"Authorization": f"Bearer {NVIDIA_KEY}"},
                {
                    "model": model or NVIDIA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 12000,
                    "temperature": 0.95,
                },
            )
            if status == 200:
                try:
                    payload = json.loads(text)
                    content = (payload["choices"][0].get("message") or {}).get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    content = ""
                if content.strip():
                    return content
        else:
            status, text = _post(
                ORCA_URL,
                {"Authorization": f"Bearer {ORCA_KEY}"},
                {
                    "model": model or ORCA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 7000,
                    "temperature": 0.95,
                },
            )
            if status == 200:
                try:
                    payload = json.loads(text)
                    content = (payload["choices"][0].get("message") or {}).get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    content = ""
                if content.strip():
                    return content
        if status in (401, 402, 403):
            return None
        if status == 0 and attempt >= retries - 2:
            return None
        time.sleep(3 + attempt * 4)
    return None


def _parse_array(text: str) -> List[Dict[str, Any]]:
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _valid(item: Dict[str, Any], tool: str) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    goal = item.get("goal")
    args = item.get("args")
    if not isinstance(goal, str) or not (3 <= len(goal.strip()) <= 220):
        return None
    if item.get("tool") != tool or not isinstance(args, dict):
        return None
    goal = re.sub(r"\s+", " ", goal).strip()
    if _find_any_entity(goal):
        return None
    ok, coerced = validate_call(tool, args)
    if not ok:
        return None
    return {"goal": goal, "tool": tool, "args": coerced}


def _task(payload: Tuple[str, str, int]) -> Tuple[str, str, List[Dict[str, Any]], Optional[str]]:
    tool, lang, count = payload
    text = _call(_prompt(tool, lang, count))
    if text is None:
        return tool, lang, [], "api error"
    items = _parse_array(text)
    accepted = []
    for item in items:
        valid = _valid(item, tool)
        if valid:
            valid["lang"] = lang
            valid["source"] = f"{_PROVIDER['name']}:{_PROVIDER['model'] or ''}".rstrip(":")
            valid["model"] = _PROVIDER["model"] or ""
            accepted.append(valid)
    if not accepted:
        return tool, lang, [], "no valid items"
    return tool, lang, accepted, None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Orca/DeepSeek core-skill distillation data")
    parser.add_argument("--per-task", type=int, default=16, help="examples per tool x language")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--provider", choices=("gemini", "openrouter", "nvidia", "orca"),
                        default="nvidia")
    parser.add_argument("--model", default=None, help="override the provider model")
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "distill" / "core_skill_v1.jsonl"))
    parser.add_argument("--tools", default="", help="comma-separated subset of tools")
    parser.add_argument("--append", action="store_true", help="merge with existing --out file")
    parser.add_argument("--limit-tools", type=int, default=0, help="debug: only first N tools")
    args = parser.parse_args(argv)

    _PROVIDER["name"] = args.provider
    _PROVIDER["model"] = args.model

    if args.tools:
        wanted = [name.strip() for name in args.tools.split(",") if name.strip()]
        unknown = [name for name in wanted if name not in TOOLS_BY_NAME]
        if unknown:
            raise SystemExit(f"unknown tools: {', '.join(unknown)}")
        tools = tuple(wanted)
    else:
        tools = TOOLS[: args.limit_tools] if args.limit_tools else TOOLS
    payloads = [(tool, lang, args.per_task) for _pass in range(args.passes)
                for tool in tools for lang in LANGS]
    print(f"tasks: {len(payloads)} (tools={len(tools)} langs={len(LANGS)} passes={args.passes})",
          flush=True)

    results: List[Dict[str, Any]] = []
    errors = 0
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for tool, lang, items, error in pool.map(_task, payloads):
            if error:
                errors += 1
                print(f"  {tool}/{lang}: {error}", flush=True)
                continue
            results.extend(items)
            print(f"  {tool}/{lang}: {len(items)}", flush=True)

    out = Path(args.out)
    if args.append and out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    seen = set()
    unique = []
    for item in results:
        key = _normalize(item.get("goal", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in unique) + "\n",
                   encoding="utf-8")
    by_tool: Dict[str, int] = {}
    by_lang: Dict[str, int] = {}
    for item in unique:
        by_tool[item["tool"]] = by_tool.get(item["tool"], 0) + 1
        by_lang[item["lang"]] = by_lang.get(item["lang"], 0) + 1
    print(f"\nvalid {len(unique)} / raw {len(results)} (tasks failed {errors}) "
          f"in {time.perf_counter() - started:.0f}s")
    print("by tool:", json.dumps(by_tool, ensure_ascii=False))
    print("by lang:", json.dumps(by_lang, ensure_ascii=False))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
