"""Read the payload back out of a built setup.exe and report what actually shipped.

The staging rules live in C# (builder/Build.cs). Judging them from the source is guessing:
this parses the central directory of the DofusPayload.zip embedded in the produced binary, so
every byte claim about the installer comes from the artifact itself.

    python tools/payload_audit.py [setup.exe]
    python tools/payload_audit.py setup.exe --gate      # enforcement, one JSON verdict
    python tools/payload_audit.py setup.exe --readers   # which model/data files nothing loads
"""
from __future__ import annotations

import collections
import os
import re
import struct
import sys
from pathlib import Path

EOCD = b"PK\x05\x06"
CENTRAL = b"PK\x01\x02"
MB = 1024 * 1024


def find_zip(blob: bytes):
    """Locate the last structurally valid End Of Central Directory record."""
    for m in reversed(list(re.finditer(re.escape(EOCD), blob))):
        e = m.start()
        if e + 22 > len(blob):
            continue
        # the 4-byte signature is not part of the record proper: unpack the 18 bytes after it
        _disk, _ddisk, entries, _here, cd_size, cd_off, _clen = struct.unpack(
            "<HHHHIIH", blob[e + 4:e + 22])
        # cd_off is relative to the start of the zip, which is unknown until we solve for it:
        # base + cd_off + cd_size must land exactly on this EOCD.
        base = e - cd_off - cd_size
        if base < 0 or base + cd_off + cd_size != e:
            continue
        if blob[base:base + 4] != b"PK\x03\x04" and blob[base:base + 4] != CENTRAL:
            continue
        return base, entries, cd_off, cd_size
    return None


def central_directory(blob: bytes, base: int, cd_off: int, cd_size: int, entries: int):
    rows = []
    i = base + cd_off
    end = base + cd_off + cd_size
    while len(rows) < entries and blob[i:i + 4] == CENTRAL:
        fname_len, extra_len, cmt_len = struct.unpack("<HHH", blob[i + 28:i + 34])
        comp, uncomp = struct.unpack("<II", blob[i + 20:i + 28])
        method, = struct.unpack("<H", blob[i + 10:i + 12])
        local, = struct.unpack("<I", blob[i + 42:i + 46])
        name = blob[i + 46:i + 46 + fname_len].decode("utf-8", "replace")
        rows.append((name.replace("\\", "/"), comp, uncomp, method, local))
        i += 46 + fname_len + extra_len + cmt_len
    return rows


def entry_bytes(blob: bytes, base: int, row) -> bytes:
    """Decompress one payload entry straight out of the built binary."""
    _name, comp, uncomp, method, local = row
    i = base + local
    if blob[i:i + 4] != b"PK\x03\x04":
        return b""
    fname_len, extra_len = struct.unpack("<HH", blob[i + 26:i + 30])
    data = blob[i + 30 + fname_len + extra_len: i + 30 + fname_len + extra_len + comp]
    if method == 0:
        return data
    try:
        import zlib
        return zlib.decompress(data, -15)
    except Exception:
        return b""


# A value that looks like a live secret. These shapes were all present in the 2026-11-03 build:
# accounts.txt carried 21 real `login:password` pairs, bin/warp/wireproxy.conf and
# wgcf-profile.conf each carried a 44-character WireGuard PrivateKey, and wgcf-account.toml
# carried a WARP access_token / license_key / private_key / device_id. The installer is the
# artifact that leaves the machine, so any of those crossing the wire hands the operator's
# credentials to every installed copy.
SECRET_PATTERNS = (
    ("wireguard-private-key", re.compile(rb"Private[Kk]ey\s*=\s*[A-Za-z0-9+/]{40,}")),
    ("warp-access-token", re.compile(rb'(?i)^access_token\s*=\s*"[^"]{8,}"', re.M)),
    ("warp-license-key", re.compile(rb'(?i)^license_key\s*=\s*"[^"]{8,}"', re.M)),
    ("warp-private-key", re.compile(rb'(?i)^private_key\s*=\s*"[^"]{8,}"', re.M)),
    ("account-credentials", re.compile(rb"^[^:\s#]{2,64}:[^:\s@]{4,64}\s*$", re.M)),
)
TEXT_SUFFIXES = (".txt", ".conf", ".toml", ".ini", ".json", ".yaml", ".yml", ".env", ".properties")
# Files that are *supposed* to hold operator data locally; the installer must ship a template.
# Exact file names that must never appear in the payload by any path, because each one held
# this machine's secret on 2026-11-03. Matched on the basename so a placeholder template
# (wireproxy.conf.template) is still allowed to ship.
SECRET_FILE_NAMES = ("wgcf-account.toml", "wgcf-profile.conf", "wireproxy.conf",
                     "wireproxy.runtime.conf")
# The enrollment tool and its template: start-network.bat tells the operator to run wgcf.exe,
# so a payload without it ships instructions that cannot be followed.
REQUIRED_TOOLS = ("bin/warp/wgcf.exe", "bin/warp/wireproxy.conf.template", "start-network.bat")
CREDENTIAL_BEARERS = ("accounts.txt", "wgcf-account.toml", "wgcf-profile.conf", "wireproxy.conf")


def scan_secrets(blob: bytes, base: int, rows) -> dict:
    """Return {file: [pattern names]} for every entry whose bytes look like a live secret."""
    hits = {}
    for row in rows:
        name = row[0].lower()
        if not name.endswith(TEXT_SUFFIXES) or row[2] > 4 * 1024 * 1024:
            continue
        data = entry_bytes(blob, base, row)
        if not data:
            continue
        found = [label for label, pat in SECRET_PATTERNS if pat.search(data)]
        if found:
            hits[row[0]] = found
    return hits


# The offline/dev tier, named once. `builder/build.cs::SkipDevPayload` must refuse the same
# strings; `agent.regression.test_dev_tier_staging_parity` proves they agree. Measured
# 2026-11-13 against the 68,086,784-byte build: 81 entries / 4,859,672 B in-zip whose only
# references are offline fit scripts, benchmark defaults, CLI editors, docstrings and dev
# reports -- no shipped runtime reader. The three encounter_wsp pkls alone are 2,399,962 B.
# `data/replay/planner_obs.jsonl` is this machine's observation log (agent/observe.py mentions
# it only in a docstring); an installed copy writes its own, exactly as it does trajectories.
# `models/duel_value_v4_0.pkl` is a default CLI argument in two offline benchmarks and a
# comment in two modules, not a load. `models/weight_edits*` and tool_policy.manifest.json are
# the argv-driven editor in agent/weight_space.py. Kept: data/kb/* (planning/kb.py),
# data/map_los/* (combat/map_field.py), models/board_pca_v1.json (combat/board_pca.py), and
# models/encounter_selfplay.pkl -- each has a real runtime reader.
DEV_TIER = (
    "models/encounter_wsp_",
    "/models/weight_edits",
    "/models/soft/",
    "models/tool_policy.manifest.json",
    "models/duel_value_v4_0.pkl",
    "/data/replay/",
    "/data/world_state_dataset/",
    "/data/auto_live/",
    "/data/wsp_scale/",
    "/data/fight_head_",
    "/session_closes.jsonl",
    "/data/cleanroom_",
    "/data/gate_report.json", "/data/gate_suites.json", "/data/gate_regression.json",
    "/data/acquisition_report.json", "/data/kb_coverage.json",
    "/data/brain_ram_baseline.json", "/data/cluster_editability.json",
    "/data/tool_policy_selflearning_verdict.json",
    "/data/fight_observation_baseline.json",
    "/data/evidence/", "/data/live_evidence/",
    "/data/cache/spells_extra.json",
    "/data/cache/dofusbook_builds_temporix_invalid",
)

# What the installer must and must not carry. Measured 2026-11-03 with this tool against the
# built artifact: the payload was 218.9 MB compressed / 439.4 MB unpacked over 773 files, of
# which 108.8 MB was models/combat_grid/model.pkl -- JAX weights the embedded interpreter
# cannot even import (it has no jax wheel, see tools/shipped_deps.py) -- plus 62.1 MB of
# recorded transitions, 13.8 MB of entity-pointer training shards, 1.9 MB of dev gate logs and
# a 10.7 MB nested copy of the interpreter the payload already ships unpacked.
FORBIDDEN = (
    *DEV_TIER,
    "models/combat_grid/",        # JAX artifact; skipped by the builder unless the runtime has jax
    "data/combat_grid",           # its synthetic training corpora
    "data/transitions",           # recorded sessions: offline inputs
    "data/entity_pointer",        # pointer-head training shards
    "data/core_sft", "data/tool_sft",
    "/trajectories/", "/kaggle/", "/docs/",
    "python_embed.zip",               # duplicate of the unpacked interpreter the payload already ships
    ".log", ".tmp", "_selftest.txt", ".bak",
    # This box's dev-tool residue. 96 files: 90 ruff cache entries, 4 pytest cache entries,
    # `.vscode/settings.json`, `dofus-network/.gitignore` and three zero-byte client staging
    # copies. An installed machine is not a git tree, so nothing can read them.
    "/.ruff_cache/", "/.pytest_cache/", "/.vscode/", "/staging-", "/.gitignore",
    # One-off run logs from probes on this box (`data/close_run{1,2}.{err,json}`,
    # `data/janitor_run.{err,json}`). The gate would also catch them via ".log"-style suffixes
    # if they grew; naming them keeps a rename from smuggling them back in.
    "/data/close_run", "/data/janitor_run",
    # Model graphs with no reader anywhere in the shipped tree -- see the zero-reference tier in
    # builder/Build.cs. The runtime forms (models/cause_head.json, its sibling prototypes,
    # models/verifier_head.json) are not forbidden.
    "models/cause_head.onnx", "models/cause_head_int8.onnx",
    "models/verifier_head_int8.onnx", "models/veto_shadow/",
    # The documented-facts history grows every time the tool that measures this artifact runs,
    # so shipping it made the payload's own unpacked size impossible to quote. See the same
    # name in builder/Build.cs's SkipDevPayload.
    "/data/doc_facts_history.json",
    # Learned weights on the opt-in tier (owner's call, 2026-11-03): 47.8 MB in-zip -- 45.5% of the
    # 100.1 MiB artifact -- for three paths that are off by default (`--policy rule`,
    # command_shadow="off", DOFUS_LEARNED_COMMAND_MODEL unset). Their wheels left requirements.txt
    # with them. The configs beside the graphs (command_onnx.json, policy.json, tokenizer.json) stay,
    # so a weight copied in by an operator still resolves.
    "models/command_encoder30/command_encoder_int8.onnx",
    "models/encoder30_v15/encoder_int8.onnx",
    "models/lexical_policy/policy.joblib",
    "models/tool_policy.onnx", "models/verifier_head.onnx",
    # The second, contradictory requirements.txt: the root file is what setup.exe and
    # install-python-ai.bat install; this one shipped five fewer packages under a stale header, and
    # anyone who pip-installed from it got an env that cannot load what the product expects.
    "dofus-network/requirements.txt",
)
REQUIRED = (
    "dofus-network/agent/", "dofus-network/ai/", "dofus-network/combat/",
    # The int8 graphs and policy.joblib are off this list on purpose: they moved to the opt-in tier
    # with the wheels that load them (see FORBIDDEN), and a REQUIRED entry would re-add the 47.8 MB
    # that decision removed.
    #
    # The two Python tiers must both cross the installer: requirements.txt is what setup.exe
    # installs (numpy + requests, 47 MiB of wheels as measured in this repo's embedded runtime) and
    # requirements-ai.txt is the opt-in tier that install-python-ai.bat reads. Losing the second
    # file would leave the sentinel unreachable.
    "requirements.txt", "requirements-ai.txt",
    "dofus-network/data/knowledge/", "dofus-network/data/cache/",
    "runtimes/python/python.exe", "client/www/",
    "requirements.txt", "dofus-net-proxy.exe", "DofusFarm.exe",
)
# Measured progression of the same artifact, all with tools/payload_audit.py against the binary:
# 224,413,696 B (214.0 MiB, 2026-10-30) -> 118,907,904 B (113.4 MiB, after the unloadable JAX
# weights, the offline corpora, the dev logs and the credential files came out) -> 107,699,712 B
# (102.7 MiB) once runtimes/python_embed.zip stopped duplicating the unpacked interpreter ->
# 104,958,976 B (100.1 MiB) at the tier split -> 57,171,968 B (54.5 MiB, 506 files) once the five
# learned weights moved to the opt-in tier with the wheels that read them.
# The cap leaves ~5.5 MiB for a genuinely new dependency and still trips on a 2018-scale regression.
BUDGET_BYTES = 60 * 1024 * 1024


def gate(rows, setup_bytes: int, secrets=None) -> dict:
    """Score a parsed payload against FORBIDDEN / REQUIRED / BUDGET_BYTES / secret shapes.

    Directory markers (zero-length entries ending in "/") are excluded from the forbidden scan:
    the invariant is that no *bytes* come from an excluded tree, and a phantom folder is a tidiness
    issue the builder prunes. They are still reported, because 16 of them in one build is what told
    me the stage was leaving empty shells behind.
    """
    files = [(n.replace("\\", "/").lower(), comp, uncomp)
             for (n, comp, uncomp, _method, _local) in rows if not n.endswith(("/", "\\"))]
    markers = len(rows) - len(files)
    names = [n for n, _c, _u in files]
    bad = {}
    for probe in FORBIDDEN:
        hits = [n for n in names if probe in n]
        if hits:
            bad[probe] = len(hits)
    wanted = [w.lower().rstrip("/") for w in REQUIRED]
    missing = [want for want in wanted if not any(want in n for n in names)]
    for tool in REQUIRED_TOOLS:
        if not any(n == tool.lower() or n.endswith("/" + tool.lower()) for n in names):
            missing.append(tool)
    # a secret file is a violation even when the content scan finds no pattern in it
    for row in rows:
        leaf = row[0].replace("\\", "/").lower().rsplit("/", 1)[-1]
        if leaf in SECRET_FILE_NAMES:
            bad["secret-file:" + leaf] = bad.get("secret-file:" + leaf, 0) + 1
    return {
        "setup_bytes": setup_bytes,
        "over_budget": setup_bytes > BUDGET_BYTES,
        "budget_bytes": BUDGET_BYTES,
        "forbidden": bad,
        "missing_required": missing,
        "entries": len(rows),
        "files": len(files),
        "empty_dir_entries": markers,
        "shipped_bytes": sum(c for _n, c, _u in files),
        "secrets": secrets or {},
        "ok": not bad and not missing and not (secrets or {}) and setup_bytes <= BUDGET_BYTES,
    }


def reader_counts(root: Path, rows):
    """How many shipped `.py` files mention each payload entry's stem.

    A report, not a gate: `models/command_encoder30/command_encoder_int8.onnx` has no reference by
    name anywhere and is still required, because `agent/command_shadow.py` reaches it through
    `command_onnx.json`. What this finds is the other kind -- a graph nothing loads through any
    route, which is what removed 2.8 MB on 2026-11-03 (§L.6). Short stems (`train`, `test`) are
    skipped because they match everything.
    """
    corpus = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("kaggle", "__pycache__", ".git", "node_modules")
                       and "toolkit" not in d]
        for fn in filenames:
            if fn.endswith(".py"):
                try:
                    corpus.append(Path(dirpath, fn).read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
    joined = "\n".join(corpus)
    out = []
    for row in rows:
        name = row[0].replace("\\", "/")
        if not name.startswith("dofus-network/"):
            continue
        tail = name[len("dofus-network/"):]
        if not (tail.startswith("models/") or tail.startswith("data/")):
            continue
        if name.endswith("/"):
            continue
        stem = tail.rsplit("/", 1)[-1].split(".")[0]
        if len(stem) < 6:
            continue
        out.append((row[1], stem in joined, tail))
    return out


def main(argv) -> int:
    path = Path(argv[0]) if argv else Path("setup.exe")
    blob = path.read_bytes()
    print("%s: %d bytes (%.1f MiB)" % (path, len(blob), len(blob) / MB))
    found = find_zip(blob)
    if found is None:
        print("no embedded zip central directory found -- this is not a payload build")
        return 1
    base, entries, cd_off, cd_size = found
    rows = central_directory(blob, base, cd_off, cd_size, entries)
    if "--readers" in argv:
        root = Path("dofus-network")
        for arg in argv[1:]:
            if not arg.startswith("--"):
                root = Path(arg)
                break
        scored = reader_counts(root, rows)
        orphans = [item for item in scored if not item[1]]
        for comp, found_any, tail in sorted(scored, key=lambda item: -item[0])[:25]:
            print("%10d %-4s %s" % (comp, "read" if found_any else "NONE", tail))
        print("scored %d entries; %d have no reader anywhere (%d B in-zip)"
              % (len(scored), len(orphans), sum(item[0] for item in orphans)))
        return 0
    if "--gate" in argv:
        import json
        secrets = scan_secrets(blob, base, rows)
        verdict = gate(rows, len(blob), secrets)
        print(json.dumps(verdict))
        if verdict["ok"]:
            print("payload gate PASS: %d files, %.1f MiB <= %.1f MiB budget, no forbidden entry, "
                  "no secret-shaped value, every required entry present"
                  % (verdict["files"], verdict["setup_bytes"] / MB, verdict["budget_bytes"] / MB))
            return 0
        print("payload gate FAILED: forbidden=%s missing=%s over_budget=%s secrets=%s (%.1f MiB)"
              % (verdict["forbidden"], verdict["missing_required"], verdict["over_budget"],
                 verdict["secrets"], verdict["setup_bytes"] / MB))
        return 1
    if "--json" in argv:
        import json
        print(json.dumps({
            "setup_bytes": len(blob),
            "declared_entries": entries,
            "entries": len(rows),
            "compressed_total": sum(r[1] for r in rows),
            "unpacked_total": sum(r[2] for r in rows),
            "names": [r[0] for r in rows],
        }))
        return 0
    comp_total = sum(r[1] for r in rows)
    raw_total = sum(r[2] for r in rows)
    print("zip base %d, declared %d entries, parsed %d" % (base, entries, len(rows)))
    print("payload: %.1f MB compressed / %.1f MB unpacked over %d files"
          % (comp_total / MB, raw_total / MB, len(rows)))
    tops = collections.Counter()
    counts = collections.Counter()
    for name, comp, uncomp, _method, _local in rows:
        key = "/".join(name.split("/")[:2])
        tops[key] += comp
        counts[key] += 1
    print("\ntop-level by shipped bytes:")
    for key, value in tops.most_common(18):
        print("  %-42s %8.1f MB  %5d files" % (key[:42], value / MB, counts[key]))
    probes = ("models/combat_grid", "data/combat_grid", "data/transitions", "data/entity_pointer",
              ".log", ".tmp", "_selftest.txt", ".bak", "runtimes/python_embed.zip",
              "data/core_sft", "data/tool_sft", "/kaggle/", "/docs/", "/trajectories/",
              "encoder_int8.onnx", "command_encoder_int8.onnx", "data/cache/", "data/knowledge/",
              "lexical_policy", "site-packages")
    print("\nprobes (what did and did not cross the wire):")
    for probe in probes:
        hits = [r for r in rows if probe in r[0].lower()]
        print("  %-28s %4d files  %8.1f MB compressed  %8.1f MB unpacked"
              % (probe, len(hits), sum(h[1] for h in hits) / MB, sum(h[2] for h in hits) / MB))
    biggest = sorted(rows, key=lambda r: -r[1])[:12]
    print("\nbiggest shipped files:")
    for name, comp, uncomp, method, _local in biggest:
        print("  %8.1f MB -> %8.1f MB  m%d  %s" % (uncomp / MB, comp / MB, method, name[:86]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
