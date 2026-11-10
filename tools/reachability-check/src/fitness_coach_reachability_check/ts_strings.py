"""Extract the literal text of TypeScript string and template literals.

A purpose-built scanner rather than a regex over raw source: paths mentioned in comments or regex
literals must not count as callers, and a template literal such as `${baseUrl}/api/x/${id}` must
keep its static text on both sides of each interpolation. It is not a TypeScript parser; it only
tracks enough lexical state (comments, quotes, template interpolation, regex literals) to tell
string text from everything else.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

# Stands in for each `${...}` in a template literal's text; it cannot occur in real source text.
INTERPOLATION = "\x00"

# A `/` after one of these characters or keywords (or at the start of the file) begins a regex
# literal rather than a division: the usual heuristic for lexing JavaScript without a full parser.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = frozenset(
    {"return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await", "delete"}
)
_CONTROL_KEYWORDS = frozenset({"if", "while", "for", "with", "switch", "catch"})
_OPERAND = "a"  # `prev` marker for "an operand just ended", after which `/` is division


@dataclass(frozen=True)
class StringSegment:
    """The decoded text of one string or template literal.

    In a template literal each `${...}` is replaced by ``INTERPOLATION``.
    """

    text: str
    line: int


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch in "_$"


@dataclass
class _CodeContext:
    prev: str = ""
    # A closing control condition starts a statement; a closing call/group ends an operand.
    parens: list[bool] = field(default_factory=list)

    def punctuation(self, ch: str) -> None:
        if ch.isspace():
            return
        if ch == "(":
            self.parens.append(self.prev in _CONTROL_KEYWORDS)
        if ch == ")" and self.parens and self.parens.pop():
            self.prev = "("
        else:
            self.prev = ch


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
        context = _CodeContext()
        while i < len(self.src):
            ch = self.src[i]
            if stop_at_brace and ch == "}" and depth == 0:
                return i + 1
            depth += {"{": 1, "}": -1}.get(ch, 0)
            new_i = self.scan_token(i, context.prev)
            if new_i != i:
                # A comment is not an operand, so it leaves the regex/division context unchanged.
                if not self.src.startswith(("//", "/*"), i):
                    context.prev = _OPERAND
                i = new_i
            elif _is_word_char(ch):
                i, context.prev = self.read_word(i)
            else:
                context.punctuation(ch)
                i += 1
        return i

    def read_word(self, i: int) -> tuple[int, str]:
        j = i
        while j < len(self.src) and _is_word_char(self.src[j]):
            j += 1
        word = self.src[i:j]
        if word in _CONTROL_KEYWORDS:
            return j, word
        return j, "(" if word in _REGEX_KEYWORDS else _OPERAND

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
                j += 1  # keep the escaped character itself, so "\/" decodes to "/"
            if j < len(self.src):
                chars.append(self.src[j])
            j += 1
        self.segments.append(StringSegment("".join(chars), self.line_of(i)))
        return j + 1

    def scan_template(self, i: int) -> int:
        j = i + 1
        chars: list[str] = []
        while j < len(self.src) and self.src[j] != "`":
            if self.src.startswith("${", j):
                chars.append(INTERPOLATION)
                j = self.scan_code(j + 2, stop_at_brace=True)
                continue
            if self.src[j] == "\\":
                j += 1
            if j < len(self.src):
                chars.append(self.src[j])
            j += 1
        self.segments.append(StringSegment("".join(chars), self.line_of(i)))
        return j + 1

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
    """Return every string/template literal in ``source``; nested templates come first."""
    return _Scanner(source).run()
