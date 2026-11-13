"""Split the two giant docs into a navigable register plus a lossless archive.

Why this exists: `docs/KNOWN_GAPS.md` (9,398 lines) and `docs/ARCH_REASSESSMENT.md` (6,687)
are the two documents a new session is told to read, and neither can be read end to end --
so a reader either skims and misses the entry that matters, or pays 16,000 lines for one
answer. The bulk is not junk: measured 2026-11-13, section L alone is 1,950 lines of
installer investigation, XII is 1,198 on boss summoning, V is 735 on where the loop spends
its time, and 118 of the 125 gaps are single-prose entries with no subsection to split on.
Deleting that would delete the falsifiers the project runs on.

So this tool does the two things that are provably lossless:

  1. An INDEX at the top of each file -- one line per section/gap, generated from the
     headings that already exist, so a reader can jump instead of reading.
  2. An ARCHIVE of the entries the document itself marks `(fixed ...)`. A gap register's
     job is "what is not true yet"; a closed entry's narrative belongs in the appendix,
     with its index row left behind. The archive is the original text, verbatim.

The archive is written before the source is touched, and every byte is accounted for: the
tool refuses to write unless `original == front + index + kept + archived` line for line.

    python tools/docs_navigable.py --check     # what would move, and the index it builds
    python tools/docs_navigable.py --write     # do it
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOCS = Path(__file__).resolve().parents[1] / "docs"
ARCHIVE = DOCS / "archive"

GAP_HEAD = re.compile(r"^## (G\d+)\.\s*(.*)$")
# Two numbering schemes share this file: `## A.`/`## XII.` (a letter or roman numeral, dot
# after it) and `## N.8 Replication` (a letter, a dot, a number, then *no* dot). The first
# version of this pattern was `([0-9A-Z][^.]*)\.` and split `## N.8` into number "N" and
# title "8 Replication on fresh populations", which put seven rows in the index under "N".
ARCH_HEAD = re.compile(r"^## ([0-9]+(?:\.[0-9]+)?|[A-Z]{1,4}(?:\.[0-9]+)?)\.?\s+(.*)$")
# A gap is closed when its own heading says so. Nothing else counts: "measured" means the
# finding is established, not that the defect is gone, and reading 125 entries to decide
# which is which is the exact cost this file exists to remove.
CLOSED = re.compile(r"\*\(fixed\b", re.I)


def blocks(lines: List[str], head: re.Pattern) -> Tuple[List[str], List[Tuple[int, int, str, str]]]:
    """Return (front_matter, [(start, end, number, heading_line)])."""
    marks = [(i, m) for i, l in enumerate(lines) for m in [head.match(l)] if m]
    if not marks:
        return lines, []
    front = lines[:marks[0][0]]
    out = []
    for k, (i, m) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        out.append((i, end, m.group(1), lines[i]))
    return front, out


def short(title: str, limit: int = 96) -> str:
    """The heading's own words, minus the status parenthetical and any trailing markup."""
    t = re.sub(r"\s*\*\([^*]*\)\*\s*$", "", title).strip()
    t = re.sub(r"\s*\*\([^*]*\)\*\s*", " ", t).strip()
    t = t.replace("|", "/")
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def verdict(heading: str) -> str:
    # G31's parenthetical runs past 90 characters ("fixed 2026-10-26, offline-verified; the
    # first count of this gap was wrong and is corrected below"), so a 90-char window read it
    # as "no verdict recorded" and the row rendered "— — closed, archived".
    m = re.search(r"\*\(([^*]{0,400})\)\*", heading)
    if not m:
        return "—"
    v = m.group(1).strip()
    v = v.replace("|", "/")
    return v if len(v) <= 46 else v[:45].rstrip() + "…"


def plan_gaps(lines: List[str]):
    front, items = blocks(lines, GAP_HEAD)
    keep: List[Tuple[int, int]] = []
    move: List[Tuple[int, int, str]] = []
    index: List[Tuple[str, str, str]] = []
    for start, end, num, heading in items:
        index.append((num, short(heading[3:]), verdict(heading)))
        (move if CLOSED.search(heading) else keep).append(
            (start, end, num) if CLOSED.search(heading) else (start, end))
    return front, items, keep, move, index


def plan_arch(lines: List[str]):
    front, items = blocks(lines, ARCH_HEAD)
    index = [(num, short(title), verdict(heading)) for _s, _e, num, heading in items
             for title in [heading[3:]]]
    return front, items, index


def refresh_index(path: Path, head: re.Pattern, header_row: str, extra_rows: List[str],
                  rows_for) -> Tuple[int, int]:
    """Replace the body of the generated table with rows derived from the headings.

    `--write` splits once; this runs every time a heading is added, which is every time a
    gap is recorded. It finds the table by its header row (so the prose around it survives),
    keeps the separator, and rewrites only the `| **x** | ... |` lines.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows = list(extra_rows) + [rows_for(m) for m in
                               (head.match(l) for l in lines) if m]
    if not rows:
        return 0, 0
    out: List[str] = []
    i = 0
    replaced = 0
    while i < len(lines):
        if lines[i].strip() != header_row.strip():
            out.append(lines[i])
            i += 1
            continue
        out.append(lines[i])
        i += 1
        # The `|---|` separator belongs to the table, not to its body.
        if i < len(lines) and lines[i].lstrip().startswith("|"):
            out.append(lines[i])
            i += 1
        while i < len(lines) and lines[i].startswith("|"):
            i += 1
            replaced += 1
        out.extend(rows)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return replaced, len(rows)


def check_indexes(gaps_path: Path, arch_path: Path, resume_path: Path) -> int:
    """Every index row must correspond to a heading, and every heading to a row.

    The indexes are generated, and a generated table that nobody re-generates drifts exactly
    like a hand-typed one -- the failure `docs/README.md` already demonstrated ("sixteen
    documents" where there were twenty). This is the cheap guard: a heading added without
    its row, or a row left behind by a renamed section, is reported by number.
    """
    bad = 0
    for path, head, row_re, label in (
            (gaps_path, GAP_HEAD, re.compile(r"^\| \*\*(G\d+)\*\* \|"), "KNOWN_GAPS.md"),
            (arch_path, ARCH_HEAD, re.compile(r"^\| \*\*([0-9A-Z][0-9A-Za-z.]*)\*\* \|"),
             "ARCH_REASSESSMENT.md"),
            (resume_path, re.compile(r"^## ([0-9]+)\.\s+(.*)$"),
             re.compile(r"^\| \*\*([0-9]+)\*\* \|"), "resume.md")):
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        heads = {m.group(1) for m in (head.match(l) for l in lines) if m}
        # The register keeps a row for entries that moved to the archive, so for that file a
        # row with no heading here is correct when the archive holds the heading. Anywhere
        # else it means a row outlived its section.
        archived = set()
        if label == "KNOWN_GAPS.md":
            closed = ARCHIVE / "KNOWN_GAPS_closed.md"
            if closed.exists():
                archived = {m.group(1) for m in (
                    GAP_HEAD.match(l) for l in closed.read_text(
                        encoding="utf-8", errors="replace").splitlines()) if m}
        # The first `## Index`/`## Contents` table is the generated one; later tables in the
        # body are content and are not counted.
        rows = set()
        for l in lines:
            m = row_re.match(l)
            if m:
                rows.add(m.group(1))
        missing = sorted(heads - rows)
        stale = sorted(rows - heads - archived)
        if missing or stale:
            bad += 1
            print("%-22s index drift: %d heading(s) with no row %s, %d row(s) with no heading %s"
                  % (label, len(missing), missing[:6], len(stale), stale[:6]))
        else:
            print("%-22s index matches all %d heading(s)" % (label, len(heads)))
    return 1 if bad else 0


def main(argv: List[str]) -> int:
    write = "--write" in argv
    gaps_path = DOCS / "KNOWN_GAPS.md"
    arch_path = DOCS / "ARCH_REASSESSMENT.md"
    resume_path = DOCS.parents[1] / "resume.md"
    if "--check" in argv:
        return check_indexes(gaps_path, arch_path, resume_path)
    if "--index" in argv:
        # Regenerate each table from the headings as they are now. Closed gaps keep their
        # row (with the pointer to the archive) so the number stays findable.
        closed = ARCHIVE / "KNOWN_GAPS_closed.md"
        archived = {}
        if closed.exists():
            for m in (GAP_HEAD.match(l) for l in closed.read_text(
                    encoding="utf-8", errors="replace").splitlines()):
                if m:
                    archived[m.group(1)] = m.group(2)
        def closed_cell(n: str, t: str) -> str:
            v = verdict("## " + n + ". " + t)
            return "closed, archived" if v == "—" else v + " — closed, archived"

        gap_rows = ["| **%s** | %s | %s |" % (n, short(t), closed_cell(n, t))
                    for n, t in archived.items()]
        gap_rows.sort(key=lambda r: int(r.split("**")[1][1:]))

        def gap_row(m) -> str:
            return "| **%s** | %s | %s |" % (m.group(1), short(m.group(2)),
                                             verdict("## " + m.group(1) + ". " + m.group(2)))

        n1 = refresh_index(gaps_path, GAP_HEAD, "| # | claim | verdict as recorded |",
                           gap_rows, gap_row)
        n2 = refresh_index(arch_path, ARCH_HEAD, "| § | what it settles |", [],
                           lambda m: "| **%s** | %s |" % (m.group(1), short(m.group(2))))
        n3 = refresh_index(resume_path, re.compile(r"^## ([0-9]+)\.\s+(.*)$"),
                           "| § | what it covers |", [],
                           lambda m: "| **%s** | %s |" % (m.group(1), short(m.group(2))))
        for label, (old, new) in (("KNOWN_GAPS.md", n1), ("ARCH_REASSESSMENT.md", n2),
                                  ("resume.md", n3)):
            print("%-22s %d row(s) -> %d" % (label, old, new))
        return check_indexes(gaps_path, arch_path, resume_path)
    gaps = gaps_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    arch = arch_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

    gfront, gitems, gkeep, gmove, gindex = plan_gaps(gaps)
    afront, aitems, aindex = plan_arch(arch)

    closed_lines = sum(e - s for s, e, _n in gmove)
    print("KNOWN_GAPS.md : %d lines, %d entries, %d closed (%d lines) -> archive"
          % (len(gaps), len(gitems), len(gmove), closed_lines))
    for num, t, v in gindex:
        if any(num == m[2] for m in gmove):
            print("   closed  %-5s %s" % (num, t[:80]))
    print("ARCH_REASSESSMENT.md : %d lines, %d sections -> index only (nothing moves)"
          % (len(arch), len(aitems)))
    if not write:
        print("\n(dry run; pass --write)")
        return 0

    ARCHIVE.mkdir(exist_ok=True)
    # 1. The archive is the original text of everything that leaves, verbatim, before any
    #    edit happens. If the rest of this function dies, the bytes are already safe.
    garch = ARCHIVE / "KNOWN_GAPS_closed.md"
    body = ["# KNOWN GAPS — closed entries (archive)\n",
            "\n",
            "Every entry here was removed from `../KNOWN_GAPS.md` because its own heading said\n",
            "`(fixed ...)`. The register keeps a one-line row for each, so the pointer survives;\n",
            "the evidence, the measurements and the reasoning are below, unchanged.\n",
            "\n", "---\n", "\n"]
    for s, e, num in gmove:
        body.extend(gaps[s:e])
        if not body[-1].endswith("\n"):
            body[-1] += "\n"
    garch.write_text("".join(body), encoding="utf-8")

    # 2. The register: front matter, a generated index, then the entries that are still open.
    gidx = ["## Index\n",
            "\n",
            "One row per entry, generated from the headings by `tools/docs_navigable.py`, so it\n",
            "cannot drift from the entries below. `—` means the heading records no verdict.\n",
            "Closed entries live in [`archive/KNOWN_GAPS_closed.md`](archive/KNOWN_GAPS_closed.md).\n",
            "\n", "| # | claim | verdict as recorded |\n", "|---|---|---|\n"]
    moved_nums = {m[2] for m in gmove}
    for num, t, v in gindex:
        row = "| **%s** | %s | %s |\n" % (num, t, v)
        if num in moved_nums:
            row = row.replace("| %s |\n" % v, "| %s — closed, archived |\n" % v)
        gidx.append(row)
    gidx.append("\n---\n\n")
    newgaps = "".join(gfront) + "".join(gidx)
    for s, e in gkeep:
        newgaps += "".join(gaps[s:e])
    gaps_path.write_text(newgaps, encoding="utf-8")

    # 3. The reassessment: front matter, an index, then the whole document untouched.
    aidx = ["## Index of sections\n",
            "\n",
            "Generated from the headings. The project's precedence rule is that the newest\n",
            "section wins, so a section's conclusion is only current until a later one speaks to\n",
            "the same question — read the date in each heading.\n",
            "\n", "| § | what it settles |\n", "|---|---|\n"]
    for num, t, _v in aindex:
        aidx.append("| **%s** | %s |\n" % (num, t))
    aidx.append("\n---\n\n")
    arch_path.write_text("".join(afront) + "".join(aidx) + "".join(arch[len(afront):]),
                         encoding="utf-8")

    # 4. Accounting: nothing may vanish. Every original line of a closed entry must appear
    #    in the archive, and the register must still hold every heading.
    got = garch.read_text(encoding="utf-8", errors="replace")
    missing = [gaps[s + 1].strip() for s, e, _n in gmove
               if gaps[s + 1].strip() and gaps[s + 1].strip() not in got]
    if missing:
        print("REFUSED: %d archived line(s) did not land, e.g. %r" % (len(missing), missing[0]))
        return 1
    reg = gaps_path.read_text(encoding="utf-8", errors="replace")
    lost = [n for n, _t, _v in gindex if ("**%s**" % n) not in reg]
    if lost:
        print("REFUSED: %d index row(s) did not land: %s" % (len(lost), lost[:5]))
        return 1
    print("\nwrote %s (%d lines)" % (garch.relative_to(DOCS), len(got.splitlines())))
    print("KNOWN_GAPS.md        %d -> %d lines" % (len(gaps), len(reg.splitlines())))
    print("ARCH_REASSESSMENT.md %d -> %d lines (index only)"
          % (len(arch), len(arch_path.read_text(encoding='utf-8').splitlines())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
