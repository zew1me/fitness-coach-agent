"""Extract the literal text of TypeScript string and template literals.

A purpose-built scanner rather than a regex over raw source: paths mentioned in comments or regex
literals must not count as callers, and a template literal such as `${baseUrl}/api/x/${id}` must
keep its static text on both sides of each interpolation. It is not a TypeScript parser; it only
tracks enough lexical state (comments, quotes, template interpolation, regex literals) to tell
string text from everything else.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

# Stands in for each `${...}` in a template literal's text; it cannot occur in real source text.
INTERPOLATION = "\x00"

# A `/` after one of these characters or keywords (or at the start of the file) begins a regex
# literal rather than a division: the usual heuristic for lexing JavaScript without a full parser.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = frozenset(
    {"return", "typeof", "case", "do", "else", "in", "void", "yield", "await", "delete"}
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


_ESCAPE = re.compile(
    r"\\(?:x(?P<hex>[0-9a-fA-F]{2})|u(?P<unicode>[0-9a-fA-F]{4})"
    r"|u\{(?P<point>[0-9a-fA-F]{1,6})\}|(?P<newline>\r\n|[\r\n])|(?P<simple>[\s\S]))"
)
_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_MAX_CODE_POINT = 0x10FFFF
_JSX_OPEN = re.compile(r"<(?:([A-Za-z_$][\w$.:-]*)(?=[\s/>])|(?=>))")


def _decode_escape(source: str, i: int) -> tuple[str, int]:
    match = _ESCAPE.match(source, i)
    if match is None:
        return "\\", i + 1
    digits = match.group("hex") or match.group("unicode") or match.group("point")
    if digits:
        point = int(digits, 16)
        text = chr(point) if point <= _MAX_CODE_POINT else match.group()
    else:
        simple = match.group("simple") or ""
        text = _SIMPLE_ESCAPES.get(simple, simple)
    return text, match.end()


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch in "_$"


@dataclass
class _CodeContext:
    prev: str = ""
    # A closing control condition starts a statement; a closing call/group ends an operand.
    parens: list[str] = field(default_factory=list)

    def punctuation(self, ch: str) -> None:
        if ch.isspace():
            return
        if ch == "(":
            self.parens.append(self.prev if self.prev in _CONTROL_KEYWORDS else "")
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
                i, context.prev = self.read_word(i, context)
            else:
                context.punctuation(ch)
                i += 1
        return i

    def read_word(self, i: int, context: _CodeContext) -> tuple[int, str]:
        j = i
        while j < len(self.src) and _is_word_char(self.src[j]):
            j += 1
        word = self.src[i:j]
        # Properties can spell keywords, including after optional chaining and comments.
        if context.prev == ".":
            return j, _OPERAND
        if word == "of" and "for" in context.parens:
            return j, "("
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
        if ch in "'\"`<":
            return self.scan_literal(i)
        if ch == "/" and (prev == "" or prev in _REGEX_PRECEDERS):
            return self.skip_regex(i)
        return i

    def scan_literal(self, i: int) -> int:
        if self.src[i] in "'\"":
            return self.scan_quoted(i)
        if self.src[i] == "`":
            return self.scan_template(i)
        return self.scan_jsx(i)

    def scan_jsx_tag(self, i: int) -> int:
        while i < len(self.src):
            ch = self.src[i]
            if ch == "{":
                i = self.scan_code(i + 1, stop_at_brace=True)
            elif ch in "'\"":
                i = self.scan_quoted(i)
            elif ch == ">":
                return i + 1
            else:
                i += 1
        return i

    def scan_jsx(self, i: int) -> int:
        opening = _JSX_OPEN.match(self.src, i)
        if opening is None:
            return i
        checkpoint = len(self.segments)
        end = self.scan_jsx_tag(opening.end())
        if self.src[end - 2 : end] == "/>":
            return end
        # Without a closing tag this may be a TypeScript assertion/generic, not JSX.
        tag = opening.group(1) or ""
        if re.search(r"</" + re.escape(tag) + r"\s*>", self.src[end:]) is None:
            del self.segments[checkpoint:]
            return i
        return self.scan_jsx_children(end)

    def scan_jsx_children(self, i: int) -> int:
        # JSX text is not JavaScript: quotes/apostrophes here do not open string literals.
        while i < len(self.src):
            if self.src.startswith("</", i):
                return self.scan_jsx_tag(i + 2)
            if self.src[i] == "{":
                i = self.scan_code(i + 1, stop_at_brace=True)
            elif self.src[i] == "<":
                i = max(i + 1, self.scan_jsx(i))
            else:
                i += 1
        return i

    def scan_quoted(self, i: int) -> int:
        quote = self.src[i]
        j = i + 1
        chars: list[str] = []
        while j < len(self.src) and self.src[j] not in (quote, "\n"):
            if self.src[j] == "\\":
                text, j = _decode_escape(self.src, j)
                chars.append(text)
            else:
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
                text, j = _decode_escape(self.src, j)
                chars.append(text)
            else:
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
