"""Pure helper functions extracted from agent/regression.py.

These functions support the regression gate without depending on test state.
Extracted to reduce regression.py's size and improve separation of concerns.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.verifier import Verifier


def code_only(text: str) -> str:
    """Source with whole-line comments removed, for the C#/JS needle checks.

    Measured twice, same trap: a needle matched a statement that had been
    commented out (the updater's User-Agent assignment, the step-row fragment
    filter), so the check passed while the code was broken and its mutation was
    MISSED. A gate that reads comments is not reading the code.
    """
    return "\n".join(line for line in text.splitlines()
                     if not line.strip().startswith("//"))


def _new_verifier() -> Any:
    """Create a verifier with a non-existent human path."""
    missing = Path(tempfile.gettempdir()) / "dofus_regression_no_human.jsonl"
    missing.unlink(missing_ok=True)
    return Verifier(model_path=None, human_path=missing, veto_threshold=0.0)


def _find_csharp_compiler() -> Optional[List[str]]:
    """The compiler builder/build.cs would pick: Roslyn from any .NET SDK, else
    the .NET Framework csc.exe."""
    dotnet = shutil.which("dotnet")
    if dotnet:
        roots = [Path(os.environ.get("ProgramFiles", "C:\\" + "Program Files")) / "dotnet" / "sdk",
                 Path(os.environ.get("ProgramFiles(x86)", "C:\\" + "Program Files (x86)")) / "dotnet" / "sdk"]
        sdks: List[Path] = []
        for root in roots:
            try:
                sdks.extend(sorted(root.iterdir(), reverse=True))
            except OSError:
                continue
        for sdk in sdks:
            csc = sdk / "Roslyn" / "bincore" / "csc.dll"
            if csc.exists():
                return [dotnet, str(csc)]
    windir = Path(os.environ.get("windir", os.environ.get("SystemRoot", "C:\\" + "Windows")))
    for flavour in ("Framework64", "Framework"):
        csc = windir / "Microsoft.NET" / flavour / os.environ.get("DOFUS_NET_FX_VERSION", "v4." + "0.30319") / "csc.exe"
        if csc.exists():
            return [str(csc)]
    return None


def _builder_host_sources(build_cs: Path) -> List[str]:
    """The host's .cs files, read out of builder/build.cs.

    The builder is the authority on what DofusFarm.exe is made of; this gate only
    borrows its answer. Parsing is deliberately narrow -- `Path.Combine(desktopDir,
    "X.cs")` is the only shape the list uses -- so a reformat that changes the
    shape returns an empty list and the gate says so instead of silently
    compiling a subset.
    """
    try:
        text = build_cs.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Only the host's list. build.cs also compiles setup.exe and uninstall.exe
    # from their own `Path.Combine(desktopDir, ...)` blocks, and taking those too
    # produced `Duplicate 'AssemblyCompany' attribute` -- a gate failure that says
    # nothing about the host.
    start = text.find("var farmSources")
    if start < 0:
        return []
    end = text.find("};", start)
    if end < 0:
        return []
    names: List[str] = []
    for match in re.finditer(
            r'Path\\.Combine\\(\\s*desktopDir\\s*,((?:\\s*\"[^\"]+\"\\s*,?)+)\\s*\\)',
            text[start:end]):
        parts = re.findall(r'\"([^\"]+)\"', match.group(1))
        name = "/".join(parts)
        if name not in names:
            names.append(name)
    return names


def _empty_catch_count(sources: List[Path]) -> Tuple[int, List[Tuple[str, int]]]:
    """Count `catch { }` blocks that swallow, and report the worst files.

    A block is empty when, after removing comments, nothing remains between the braces --
    which is what makes an exception unobservable rather than handled.
    """
    total = 0
    per_file: List[Tuple[str, int]] = []
    for path in sources:
        text = path.read_text(encoding="utf-8", errors="replace")
        n = 0
        for m in re.finditer(r"catch\s*(?:\([^)]*\))?\s*\{", text):
            i = m.end()
            depth, j = 1, i
            while j < len(text) and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            body = re.sub(r"//[^\n]*|/\*.*?\*/", "", text[i:j - 1], flags=re.S).strip()
            if not body:
                n += 1
        if n:
            per_file.append((path.name, n))
        total += n
    per_file.sort(key=lambda row: -row[1])
    return total, per_file