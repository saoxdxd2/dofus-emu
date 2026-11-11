"""Fail when a source literal names a tracked file with the wrong case.

`builder/build.cs` said `Path.Combine(desktopDir, "MainForm.cs")` while git
stored `desktop/mainform.cs`. Windows resolves either spelling, so the builder
worked, and the gate's own "every builder source file exists" check passed --
on the one machine that cannot see the bug. A Linux checkout has the lowercase
names, so the same check fails on files that are right there, and the failure
looks like a missing file rather than a casing mistake.

This compares the C# path literals in the builder and the gate against the names
`git ls-files` reports. It also reports two tracked paths that differ only by
case, which a case-insensitive checkout cannot represent at all.

    python tools/check_case.py          # exit 0 clean, 1 on drift
    python tools/check_case.py -v       # list every literal checked

Not a git checkout (a tarball, an exported zip): exits 0 with a note, because
the question is about the index and there is no index to ask.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The files that open C# sources by name. Kept explicit: a repo-wide scan of
# every quoted string would report strings that are not paths.
SOURCES = (
    "builder/build.cs",
    "compile.bat",
    "dofus-network/agent/regression.py",
    "dofus-network/kaggle/toolkit/agent/regression.py",
)

# `Path.Combine(desktopDir, "MainForm.cs")` -- the shape builder/build.cs uses
# for both the host and the installer source lists.
DESKTOP_DIR = re.compile(r'Path\.Combine\(\s*desktopDir\s*,\s*"([A-Za-z0-9_.]+\.cs)"\s*\)')
# `Path.Combine(RepoRoot, "builder", "Build.cs")` -- the builder locating itself.
TWO_PART = re.compile(r'Path\.Combine\(\s*[A-Za-z_]+Root\s*,\s*"(builder|desktop)"\s*,\s*"([A-Za-z0-9_.]+\.cs)"\s*\)')
# `ROOT / "desktop" / "Program.cs"` and the `Path / "x" / "y"` variant.
SLASH = re.compile(r'"(builder|desktop)"\s*/\s*"([A-Za-z0-9_.]+\.cs)"')
# `builder\build.cs` in compile.bat (backslash is a literal in a batch file).
BATCH = re.compile(r'\b(builder|desktop)\\([A-Za-z0-9_.]+\.cs)\b')


def tracked_files() -> set[str] | None:
    try:
        out = subprocess.check_output(["git", "-C", str(REPO), "ls-files", "-z"],
                                      stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    return {p for p in out.decode("utf-8", "replace").split("\0") if p}


def literals(text: str, name: str) -> list[tuple[str, str]]:
    """Every (expected repo path, literal) pair a file names."""
    found: list[tuple[str, str]] = []
    for match in DESKTOP_DIR.finditer(text):
        found.append(("desktop/" + match.group(1), match.group(1)))
    for match in TWO_PART.finditer(text):
        found.append((match.group(1) + "/" + match.group(2), match.group(2)))
    for match in SLASH.finditer(text):
        found.append((match.group(1) + "/" + match.group(2), match.group(2)))
    if name.endswith(".bat"):
        for match in BATCH.finditer(text):
            found.append((match.group(1) + "/" + match.group(2), match.group(2)))
    # `_builder_host_sources`' docstring spells the shape as `"X.cs"`. A stem with
    # no lowercase letter is a placeholder, not a filename -- every tracked .cs
    # file here has one, including `Ini.cs`.
    return [(path, literal) for path, literal in found
            if any(ch.islower() for ch in literal.rsplit(".", 1)[0])]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every literal, not just the wrong ones")
    args = parser.parse_args(argv)

    tracked = tracked_files()
    if tracked is None:
        print("case check: not a git checkout (or no git), skipped")
        return 0

    # A case-insensitive checkout cannot hold both `mainform.cs` and `MainForm.cs`;
    # git can, and then every checkout silently keeps whichever one it wrote first.
    by_lower: dict[str, list[str]] = defaultdict(list)
    for path in tracked:
        by_lower[path.lower()].append(path)
    collisions = {k: sorted(v) for k, v in by_lower.items() if len(v) > 1}

    problems: list[str] = []
    checked = 0
    for rel in SOURCES:
        path = REPO / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for expected, literal in literals(text, rel):
            checked += 1
            if expected in tracked:
                if args.verbose:
                    print("ok   %s: %s" % (rel, literal))
                continue
            # Same file, different case? That is the bug this exists for.
            same = by_lower.get(expected.lower())
            if same:
                problems.append("%s: %r should be %r (git stores %s)"
                                % (rel, literal, Path(same[0]).name, same[0]))
            else:
                problems.append("%s: %r names no tracked file (%s)"
                                % (rel, literal, expected))

    for paths in collisions.values():
        problems.append("tracked paths differ only by case: %s" % ", ".join(paths))

    print("case check: %d literal(s) in %d file(s)" % (checked, len(SOURCES)))
    if problems:
        for line in problems:
            print("FAIL " + line)
        print("\n%d case problem(s)" % len(problems))
        return 1
    print("every C# path literal matches the name git stores")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
