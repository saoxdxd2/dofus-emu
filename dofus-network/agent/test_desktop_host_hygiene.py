"""Two things in the C# host that fail silently and are worth a gate.

**Swallowed exceptions.** A `catch { }` in the host swallows a WebView2 or UI
failure with no trace, which is exactly the class of bug that is invisible until
a live run and unreproducible afterwards.

The detector has one hard rule that most naive versions of this check get wrong:
a `catch {}` inside a C# *string literal* is JavaScript, not a swallowed
exception. `desktop/WebView2ClientForm.cs` embeds a large injected script, and
15 of the 16 matches in this tree were in that script. Flagging those would
force somebody to "fix" working JavaScript, and a gate that cries wolf gets
disabled. So the scanner strips C# string literals first.

**Per-call regex construction.** `new Regex(...)` inside a method allocates and
re-compiles on every call. Hoisting to a `static readonly` field is the fix, and
this pins that every `new Regex(` in the host sits in a field initialiser.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent.parent.parent / "desktop"

#: Source files that are not the host application (builders, not the app).
_EXCLUDED = {"installer.cs", "uninstaller.cs"}


def _sources() -> list[Path]:
    if not DESKTOP.is_dir():
        return []
    return sorted(p for p in DESKTOP.glob("*.cs") if p.name not in _EXCLUDED)


def _strip_strings_and_comments(text: str) -> str:
    """Blank out C# string literals and comments, preserving line structure.

    Line structure is preserved (newlines are kept, the rest replaced with
    spaces) so a reported line number still points at the real line.

    Handles the forms this tree uses: `"..."` with backslash escapes, `@"..."`
    verbatim strings with `""` as an escaped quote, and `$"..."`. An
    interpolated string can contain a nested expression with a real `catch` in
    it, but there is none in this tree, and treating the whole literal as text
    is the conservative choice -- it can only hide a swallow, never invent one.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_line_comment = in_block_comment = False
    verbatim = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                out.append(ch)
            else:
                out.append(" ")
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                out.append("  ")
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if verbatim:
            if ch == '"' and nxt == '"':
                out.append("  ")
                i += 2
                continue
            if ch == '"':
                verbatim = False
                out.append(" ")
                i += 1
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            out.append("  ")
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            out.append("  ")
            i += 2
            continue
        if ch == "@" and nxt == '"':
            verbatim = True
            out.append("  ")
            i += 2
            continue
        if ch == '"':
            # A plain or interpolated literal; skip to its closing quote.
            i += 1
            out.append(" ")
            while i < n:
                c = text[i]
                if c == "\\":
                    out.append("  ")
                    i += 2
                    continue
                if c == '"':
                    out.append(" ")
                    i += 1
                    break
                out.append("\n" if c == "\n" else " ")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _silent_catches(code: str) -> list[int]:
    """Line numbers of `catch { }` whose body is only whitespace or comments."""
    lines = code.split("\n")
    found: list[int] = []
    for match in re.finditer(r"catch\s*(?:\([^)]*\))?\s*\{", code):
        start = match.end()
        depth, i = 1, start
        while i < len(code) and depth:
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
            i += 1
        body = code[start:i - 1]
        # Comments were already blanked by `_strip_strings_and_comments`.
        if body.strip() == "":
            found.append(code[:match.start()].count("\n") + 1)
    return found


class SwallowedExceptionTests(unittest.TestCase):

    def test_no_catch_block_swallows_its_exception(self):
        offenders = []
        for path in _sources():
            code = _strip_strings_and_comments(
                path.read_text(encoding="utf-8", errors="replace"))
            for line in _silent_catches(code):
                offenders.append("%s:%d" % (path.name, line))
        self.assertFalse(
            offenders,
            "a catch block discards its exception and its message; log it "
            "(Debug.WriteLine(\"[WARN] ...\")) and keep going: %s"
            % ", ".join(offenders))

    def test_the_scanner_ignores_javascript_embedded_in_a_csharp_string(self):
        """The 15 JS catches in the injected script must not be flagged."""
        sample = '''
        private const string Script = @"
            try { x(); } catch { }
            try { y(); } catch (e) { }
        ";
        '''
        code = _strip_strings_and_comments(sample)
        self.assertEqual(_silent_catches(code), [])

    def test_the_scanner_still_finds_a_real_one(self):
        sample = '''
        void A() {
            try { Run(); }
            catch { }
        }
        '''
        code = _strip_strings_and_comments(sample)
        self.assertEqual(len(_silent_catches(code)), 1)

    def test_the_scanner_finds_one_that_only_holds_a_comment(self):
        """A comment is not a log line -- this exact form shipped once."""
        sample = '''
        try { Load(); }
        catch { /* must not stop the app */ }
        '''
        code = _strip_strings_and_comments(sample)
        self.assertEqual(len(_silent_catches(code)), 1)

    def test_a_logged_catch_is_accepted(self):
        sample = '''
        try { Load(); }
        catch (Exception ex) { Debug.WriteLine("[WARN] x: " + ex.Message); }
        '''
        code = _strip_strings_and_comments(sample)
        self.assertEqual(_silent_catches(code), [])


class RegexHoistingTests(unittest.TestCase):

    def test_every_regex_in_the_host_is_a_field_not_a_call(self):
        """`new Regex(...)` inside a method recompiles on every call."""
        offenders = []
        for path in _sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.split("\n")
            for match in re.finditer(r"new Regex\(", text):
                line = match.start()
                number = text[:line].count("\n") + 1
                # A field initialiser names the declaration on the same or the
                # preceding line, and never sits inside a statement body. The
                # cheap discriminator for this tree: the declaration is
                # `static readonly Regex <Name> =` (possibly wrapped).
                window = "\n".join(lines[max(0, number - 3):number])
                if not re.search(r"(static|readonly)\s+(static\s+)?readonly\s+Regex\s+\w+",
                                 window):
                    offenders.append("%s:%d" % (path.name, number))
        self.assertFalse(
            offenders,
            "hoist these into `private static readonly Regex`: %s"
            % ", ".join(offenders))


if __name__ == "__main__":
    unittest.main(verbosity=2)
