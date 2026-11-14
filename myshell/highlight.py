"""Syntax coloring for the line editor. No prompt_toolkit import here.

tokenize_for_color() cuts a line into (style_class, text) pieces. The pieces always join
back to the exact input, and half-typed input (an open quote, "$(", a trailing
backslash) never raises."""
import re
from typing import Callable, Dict, List, Tuple

STYLES: Dict[str, str] = {
    "command": "fg:ansigreen",
    "badcommand": "fg:ansired",
    "keyword": "fg:ansiblue bold",
    "string": "fg:ansiyellow",
    "variable": "fg:ansicyan",
    "operator": "fg:ansimagenta",
    "comment": "fg:ansibrightblack",
    "plain": "",
}

KEYWORDS = frozenset("if then elif else fi while until for do done in case esac function { }".split())
# After these, the next word is a command again.
OPENERS = frozenset("then do else if elif while until {".split())
OPERATORS = ("&>>", "2>&1", "1>&2", "<<<", "<<-", "2>>", "1>>", "&&", "||", ";;", "&>", "<<",
             ">>", ">&", "<&", "2>", "1>", "|", "&", ";", ">", "<")
WORD_END = " \t\r\n|&;<>()"
VAR_RE = re.compile(r"\$(\{[^}]*\}?|[A-Za-z_][A-Za-z0-9_]*|[0-9@?$!#*-])")
ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")

Fragment = Tuple[str, str]


def _skip_parens(text: str, i: int) -> int:
    """text[i] is '(' after '$'. Return the index just past the matching ')' (or the end)."""
    depth = 0
    quote = ""
    n = len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = ""
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _read_word(text: str, i: int) -> Tuple[int, List[Fragment], bool]:
    """Read one shell word starting at i. Returns (end, fragments, is_simple); simple
    means no quotes, escapes or expansions."""
    frags: List[Fragment] = []
    n = len(text)
    simple = True
    plain_start = i

    def flush(upto: int) -> None:
        if upto > plain_start:
            frags.append(("plain", text[plain_start:upto]))

    while i < n:
        c = text[i]
        if c in WORD_END:
            break
        if c == "\\":
            simple = False
            i = min(i + 2, n)
        elif c == "'":
            simple = False
            flush(i)
            end = text.find("'", i + 1)
            end = n if end < 0 else end + 1
            frags.append(("string", text[i:end]))
            i = plain_start = end
        elif c == '"':
            simple = False
            flush(i)
            j = i + 1
            seg = i
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 2
                    continue
                m = VAR_RE.match(text, j) if text[j] == "$" else None
                if m:
                    if j > seg:
                        frags.append(("string", text[seg:j]))
                    frags.append(("variable", m.group()))
                    j = seg = m.end()
                elif text.startswith("$(", j):
                    j = _skip_parens(text, j + 1)
                else:
                    j += 1
            end = min(j + 1, n)
            if end > seg:
                frags.append(("string", text[seg:end]))
            i = plain_start = end
        elif c == "`":
            simple = False
            end = text.find("`", i + 1)
            i = n if end < 0 else end + 1
        elif c == "$":
            simple = False
            if text.startswith("$(", i):
                i = _skip_parens(text, i + 1)
                continue
            m = VAR_RE.match(text, i)
            if m:
                flush(i)
                frags.append(("variable", m.group()))
                i = plain_start = m.end()
            else:
                i += 1
        else:
            i += 1
    flush(i)
    return i, frags, simple


def _merge(frags: List[Fragment]) -> List[Fragment]:
    out: List[Fragment] = []
    for cls, s in frags:
        if not s:
            continue
        if out and out[-1][0] == cls:
            out[-1] = (cls, out[-1][1] + s)
        else:
            out.append((cls, s))
    return out


def _tokenize(text: str, kind_of: Callable[[str], str]) -> List[Fragment]:
    out: List[Fragment] = []
    n = len(text)
    i = 0
    at_cmd = True
    pending = 0  # for/case: variable word, then "in"
    after_function = False
    while i < n:
        c = text[i]
        if c in " \t\r":
            j = i
            while j < n and text[j] in " \t\r":
                j += 1
            out.append(("plain", text[i:j]))
            i = j
            continue
        if c == "\n":
            out.append(("plain", c))
            i += 1
            at_cmd = True
            pending = 0
            continue
        if c == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(("comment", text[i:j]))
            i = j
            continue
        op = next((o for o in OPERATORS if text.startswith(o, i)), None)
        if op:
            out.append(("operator", op))
            i += len(op)
            if op in ("|", "||", "&&", ";", ";;", "&"):
                at_cmd = True
                pending = 0
            continue
        if c in "()":
            out.append(("plain", c))
            i += 1
            at_cmd = True  # after "(" or "f()" a command follows
            continue
        end, frags, simple = _read_word(text, i)
        if end == i:  # cannot happen, but never loop forever
            end = i + 1
            frags = [("plain", text[i:end])]
        word = text[i:end]
        i = end
        if pending == 2:
            out.extend(frags)
            pending = 1
            at_cmd = False
        elif pending == 1 and word == "in":
            out.append(("keyword", word))
            pending = 0
        elif after_function:
            out.extend(frags)
            after_function = False
            at_cmd = True  # "{" or "()" follows
        elif at_cmd and simple and word in KEYWORDS:
            out.append(("keyword", word))
            at_cmd = word in OPENERS
            if word in ("for", "case"):
                pending = 2
                at_cmd = False
            elif word == "function":
                after_function = True
        elif at_cmd and simple and ASSIGN_RE.match(word):
            out.extend(frags)  # NAME=value stays at command position
        elif at_cmd and simple:
            out.append(("command" if kind_of(word) == "command" else "badcommand", word))
            at_cmd = False
        else:
            out.extend(frags)
            at_cmd = False
    return out


def tokenize_for_color(text: str, kind_of: Callable[[str], str]) -> List[Tuple[str, str]]:
    """Cut `text` into (style_class, text) pieces that join back to `text`.
    kind_of(word) returns "command" for a known command and anything else otherwise."""
    try:
        result = _merge(_tokenize(text, kind_of))
        if "".join(s for _, s in result) == text:
            return result
    except Exception:
        pass
    return [("plain", text)] if text else []
