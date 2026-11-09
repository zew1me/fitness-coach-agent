"""Extract the static text of TypeScript string and template literals.

A purpose-built scanner rather than a regex over raw source: paths mentioned in comments must not
count as callers, and a template literal such as `${baseUrl}/api/chat/model-state` must yield its
static `/api/...` segment. It is not a TypeScript parser; it only tracks enough lexical state
(comments, quotes, template interpolation, regex literals) to tell string text from everything else.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

# A `/` after one of these characters (or at the start of the file) begins a regex literal rather
# than a division, which is the usual heuristic for lexing JavaScript without a full parser.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%<>~^")


@dataclass(frozen=True)
class StringSegment:
    """One static run of literal text.

    ``interpolated`` is true when the run is cut short by a template `${...}` expression, so the
    full runtime string is only known to start with ``text``.
    """

    text: str
    interpolated: bool
    line: int


class _Scanner:
    def __init__(self, source: str) -> None:
        self.src = source
        self.segments: list[StringSegment] = []
        self._newlines = [i for i, ch in enumerate(source) if ch == "\n"]

    def line_of(self, offset: int) -> int:
        return bisect_right(self._newlines, offset - 1) + 1

    def run(self) -> list[StringSegment]:
        self.scan_code(0, stop_at_brace=False)
        return self.segments

    def scan_code(self, i: int, *, stop_at_brace: bool) -> int:
        """Scan code until EOF or, inside `${...}`, the brace that closes the interpolation."""
        depth = 0
        prev = ""
        while i < len(self.src):
            ch = self.src[i]
            if stop_at_brace and ch == "}" and depth == 0:
                return i + 1
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            new_i = self.scan_token(i, prev)
            if new_i == i:
                if not ch.isspace():
                    prev = ch
                i += 1
            else:
                prev = "a"  # a literal or comment ends an expression operand
                i = new_i
        return i

    def scan_token(self, i: int, prev: str) -> int:
        """Consume a comment or literal starting at ``i``; return ``i`` unchanged otherwise."""
        src = self.src
        if src.startswith("//", i):
            end = src.find("\n", i)
            return len(src) if end == -1 else end
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            return len(src) if end == -1 else end + 2
        ch = src[i]
        if ch in "'\"":
            return self.scan_quoted(i)
        if ch == "`":
            return self.scan_template(i)
        if ch == "/" and (prev == "" or prev in _REGEX_PRECEDERS):
            return self.skip_regex(i)
        return i

    def scan_quoted(self, i: int) -> int:
        quote = self.src[i]
        j = i + 1
        chars: list[str] = []
        while j < len(self.src) and self.src[j] not in (quote, "\n"):
            if self.src[j] == "\\":
                j += 1
            elif j < len(self.src):
                chars.append(self.src[j])
            j += 1
        self.segments.append(StringSegment("".join(chars), False, self.line_of(i)))
        return j + 1

    def scan_template(self, i: int) -> int:
        j = i + 1
        start = j
        chars: list[str] = []
        while j < len(self.src):
            ch = self.src[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "`":
                self.segments.append(StringSegment("".join(chars), False, self.line_of(start)))
                return j + 1
            if self.src.startswith("${", j):
                self.segments.append(StringSegment("".join(chars), True, self.line_of(start)))
                j = self.scan_code(j + 2, stop_at_brace=True)
                chars = []
                start = j
                continue
            chars.append(ch)
            j += 1
        return j

    def skip_regex(self, i: int) -> int:
        j = i + 1
        in_class = False
        while j < len(self.src) and self.src[j] != "\n":
            ch = self.src[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                return j + 1
            j += 1
        # Not a terminated regex on this line: treat the slash as an operator after all.
        return i + 1


def string_segments(source: str) -> list[StringSegment]:
    """Return every static string/template segment in ``source``, in source order."""
    return _Scanner(source).run()
