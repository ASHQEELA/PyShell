"""Lexer: turns a line of text into tokens. Quotes and operators are resolved here."""
from dataclasses import dataclass, field
from typing import List, Tuple

from .errors import ShellSyntaxError


WORD, PIPE, AND, OR, SEMI, BG, REDIR = "WORD", "PIPE", "AND", "OR", "SEMI", "BG", "REDIR"

# Longest operators first so "&>>" wins over "&>" and "&".
OPERATORS: List[Tuple[str, str]] = [
    ("&>>", REDIR), ("2>>", REDIR), ("1>>", REDIR),
    ("2>&1", REDIR), ("1>&2", REDIR), ("<<<", REDIR), ("<<-", REDIR),
    ("<<", REDIR),
    ("&&", AND), ("||", OR), ("&>", REDIR), ("2>", REDIR), ("1>", REDIR),
    (">>", REDIR), (">&2", REDIR), (">&1", REDIR), ("<", REDIR),
    ("|", PIPE), (";", SEMI), ("&", BG), (">", REDIR),
]
# Operators that start with a digit only count at the start of a word.
FD_OPERATORS = ("2>>", "1>>", "2>&1", "1>&2", "2>", "1>")
COMMAND_START_AFTER = (PIPE, AND, OR, SEMI, BG)


@dataclass
class Token:
    kind: str
    # Each part is (text, mode). Mode is "plain", "single" or "double".
    parts: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(p[0] for p in self.parts)

    @property
    def quoted(self) -> bool:
        return any(mode != "plain" for _, mode in self.parts)


def find_matching_paren(text: str, start: int) -> int:
    """text[start] is '('. Return the index of the matching ')'."""
    depth = 0
    quote = None
    i = start
    while i < len(text):
        c = text[i]
        if quote == "'":
            if c == "'":
                quote = None
        elif quote == "`":
            if c == "\\":
                i += 1
            elif c == "`":
                quote = None
        elif quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = None
        elif c == "\\":
            i += 1
        elif c in "'\"`":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ShellSyntaxError("unterminated command substitution")


def read_backticks(text: str, start: int) -> Tuple[str, int]:
    """text[start] is a backtick. Return (command text, index of the closing backtick).
    Inside backticks a backslash before $, ` or \\ is removed. Other backslashes stay."""
    out = ""
    i = start + 1
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text) and text[i + 1] in "$`\\":
            out += text[i + 1]
            i += 2
        elif c == "`":
            return out, i
        else:
            out += c
            i += 1
    raise ShellSyntaxError("unterminated command substitution")


def lex(line: str) -> List[Token]:
    tokens: List[Token] = []
    parts: List[Tuple[str, str]] = []
    buf = ""
    mode = "plain"
    has_word = False
    i, n = 0, len(line)

    def flush_buf():
        nonlocal buf
        if buf:
            parts.append((buf, mode))
            buf = ""

    def close_quote():
        # Unlike flush_buf, this keeps an empty part so that "" survives as an argument.
        nonlocal buf
        parts.append((buf, mode))
        buf = ""

    def end_word():
        nonlocal parts, has_word
        flush_buf()
        if has_word:
            tokens.append(Token(WORD, parts))
        parts = []
        has_word = False

    while i < n:
        c = line[i]
        if mode == "single":
            if c == "'":
                close_quote()
                mode = "plain"
            else:
                buf += c
            i += 1
        elif mode == "double":
            if c == '"':
                close_quote()
                mode = "plain"
                i += 1
            elif c == "$" and line.startswith("$(", i):
                end = find_matching_paren(line, i + 1)
                buf += line[i:end + 1]  # kept whole. Expansion runs it later.
                i = end + 1
            elif c == "`":
                inner, end = read_backticks(line, i)
                buf += "$(" + inner + ")"  # same as $(...) from here on
                i = end + 1
            elif c == "\\" and i + 1 < n and line[i + 1] in '"\\$`':
                if line[i + 1] == "$":
                    # An escaped dollar sign stays literal. Store it as its own part.
                    flush_buf()
                    parts.append(("$", "single"))
                else:
                    buf += line[i + 1]
                i += 2
            else:
                buf += c
                i += 1
        else:
            if c == "'" or c == '"':
                flush_buf()
                mode = "single" if c == "'" else "double"
                has_word = True
                i += 1
            elif c == "$" and line.startswith("$(", i):
                end = find_matching_paren(line, i + 1)
                buf += line[i:end + 1]  # kept whole. Expansion runs it later.
                has_word = True
                i = end + 1
            elif c == "`":
                inner, end = read_backticks(line, i)
                buf += "$(" + inner + ")"
                has_word = True
                i = end + 1
            elif c == "\\":
                flush_buf()
                if i + 1 < n:
                    parts.append((line[i + 1], "single"))
                    i += 2
                else:
                    parts.append(("\\", "single"))
                    i += 1
                has_word = True
            elif c.isspace():
                end_word()
                i += 1
            elif c == "#" and not has_word and not buf:
                break  # a comment runs to the end of the line
            else:
                matched = None
                for op, kind in OPERATORS:
                    if not line.startswith(op, i):
                        continue
                    if op in FD_OPERATORS and (has_word or buf):
                        continue
                    matched = (op, kind)
                    break
                if matched:
                    end_word()
                    op, kind = matched
                    tokens.append(Token(kind, [(op, "plain")]))
                    i += len(op)
                else:
                    buf += c
                    has_word = True
                    i += 1

    if mode != "plain":
        raise ShellSyntaxError("unterminated quote")
    end_word()
    return tokens
