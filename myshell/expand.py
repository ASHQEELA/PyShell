"""Expansion: variables, $(...), word splitting, globbing and tilde."""
import glob
import os
import re
import sys
from typing import List, Optional, Tuple

from . import compat
from .arith import evaluate
from .errors import ShellError, ShellExit
from .lexer import WORD, Token, find_matching_paren, read_backticks
from .state import ShellState


VAR_RE = re.compile(r"\$(\?|\$|[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})")
ASSIGN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)


def command_substitution(text: str, state: ShellState) -> str:
    """Run text in a subshell and return its output without trailing newlines.
    The subshell is a forked copy of this shell, so aliases, builtins and
    variables work. Changes it makes (cd, exports) do not reach the parent."""
    if not compat.HAS_FORK:
        raise ShellError("command substitution is not supported on this platform")
    sys.stdout.flush()
    sys.stderr.flush()
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        status = 1
        try:
            os.close(read_fd)
            os.dup2(write_fd, 1)
            os.close(write_fd)
            # Fresh file objects, so output reaches fd 1 even if sys.stdout was replaced.
            sys.stdout = os.fdopen(1, "w", closefd=False)
            sys.stderr = os.fdopen(2, "w", buffering=1, closefd=False)
            state.jobs.interactive = False  # a subshell never takes over the terminal
            status = state.execute(text)
        except ShellExit as e:
            status = e.code
        except ShellError as e:
            print(e, file=sys.stderr)
            status = 2
        except BaseException:
            status = 1
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            os._exit(status & 0xFF)
    os.close(write_fd)
    chunks: List[bytes] = []
    try:
        while True:
            data = os.read(read_fd, 65536)
            if not data:
                break
            chunks.append(data)
    finally:
        os.close(read_fd)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    return b"".join(chunks).decode(errors="replace").rstrip("\n")


def variable_value(name: str, state: ShellState) -> str:
    if name == "?":
        return str(state.last_status)
    if name == "$":
        return str(os.getpid())
    if name.startswith("{"):
        name = name[1:-1]
    return state.get_variable(name) or ""


def arithmetic(expr: str, state: ShellState) -> str:
    """Expand $VAR and $(...) inside expr, then evaluate it as integer math."""
    expr = expand_vars(expr, state)
    return str(evaluate(expr, state.get_variable, state.set_variable))


def expand_dollar(text: str, i: int, state: ShellState) -> Optional[Tuple[str, int]]:
    """text[i] is '$'. If an expansion starts here, return (value, index after it).
    Handles $((...)), $(...), $VAR, ${VAR}, $? and $$. A lone '$' returns None."""
    if text.startswith("$(", i):
        end = find_matching_paren(text, i + 1)
        # $(( ... )) is math when the inner pair closes right before the outer one.
        if text.startswith("$((", i) and find_matching_paren(text, i + 2) == end - 1:
            return arithmetic(text[i + 3:end - 1], state), end + 1
        return command_substitution(text[i + 2:end], state), end + 1
    m = VAR_RE.match(text, i)
    if m:
        return variable_value(m.group(1), state), m.end()
    return None


def expand_segments(text: str, state: ShellState) -> List[Tuple[str, bool]]:
    """Expand $VAR, $(...) and $((...)) in text. Returns (string, came_from_expansion) pieces."""
    segments: List[Tuple[str, bool]] = []
    literal = ""
    i = 0
    while i < len(text):
        if text[i] == "$":
            found = expand_dollar(text, i, state)
            if found is not None:
                value, i = found
                if literal:
                    segments.append((literal, False))
                    literal = ""
                segments.append((value, True))
                continue
        literal += text[i]
        i += 1
    if literal:
        segments.append((literal, False))
    return segments


def expand_heredoc(body: str, state: ShellState) -> str:
    """Expand the body of an unquoted here-document. Quotes stay literal.
    A backslash before $, ` or \\ removes the backslash. Backslash-newline joins lines."""
    out: List[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body) and body[i + 1] in "$`\\":
            out.append(body[i + 1])
            i += 2
        elif c == "\\" and body.startswith("\\\n", i):
            i += 2
        elif c == "$":
            found = expand_dollar(body, i, state)
            if found is None:
                out.append(c)
                i += 1
            else:
                out.append(found[0])
                i = found[1]
        elif c == "`":
            inner, end = read_backticks(body, i)
            out.append(command_substitution(inner, state))
            i = end + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def expand_vars(text: str, state: ShellState) -> str:
    """Expand text that is inside double quotes. The result is never split."""
    return "".join(seg for seg, _ in expand_segments(text, state))


def has_glob_chars(text: str) -> bool:
    return any(ch in text for ch in "*?[")


class _Fields:
    """Collects the words one token expands to."""

    def __init__(self):
        self.done: List[Tuple[str, str, bool]] = []  # (value, glob pattern, has_glob)
        self._reset()

    def _reset(self) -> None:
        self.value = ""
        self.pattern = ""
        self.has_glob = False
        self.present = False  # true once a quoted part is added, even an empty one

    def add(self, text: str, pattern: str, has_glob: bool = False, quoted: bool = False) -> None:
        self.value += text
        self.pattern += pattern
        self.has_glob = self.has_glob or has_glob
        if quoted:
            self.present = True

    def end_field(self) -> None:
        if self.value or self.present:  # an empty unquoted field disappears
            self.done.append((self.value, self.pattern, self.has_glob))
        self._reset()


def expand_word(tok: Token, state: ShellState, globbing: bool = True,
                splitting: bool = True) -> List[str]:
    """Expand one word token into zero or more strings.
    Unquoted $VAR and $(...) results split on whitespace and may match files.
    Quoted text and literal text never split."""
    fields = _Fields()
    for idx, (text, mode) in enumerate(tok.parts):
        if mode == "single":
            fields.add(text, glob.escape(text), quoted=True)
        elif mode == "double":
            t = expand_vars(text, state)
            fields.add(t, glob.escape(t), quoted=True)
        else:
            if idx == 0:
                text = os.path.expanduser(text)
            for seg, from_expansion in expand_segments(text, state):
                if from_expansion and splitting:
                    pieces = re.split(r"(\s+)", seg)  # odd positions hold the whitespace
                    for k, piece in enumerate(pieces):
                        if k % 2:
                            fields.end_field()
                        elif piece:
                            fields.add(piece, piece, globbing and has_glob_chars(piece))
                else:
                    fields.add(seg, seg, globbing and has_glob_chars(seg))
    fields.end_field()

    result: List[str] = []
    for value, pattern, has_glob in fields.done:
        if has_glob:
            matches = sorted(glob.glob(pattern))
            if matches:
                result.extend(matches)
                continue
        result.append(value)
    return result


def expand_tokens(tokens: List[Token], state: ShellState) -> List[str]:
    result: List[str] = []
    for t in tokens:
        result.extend(expand_word(t, state))
    return result


def expand_value(tok: Token, state: ShellState) -> str:
    """Expand an assignment value. No globbing."""
    return "".join(expand_word(tok, state, globbing=False, splitting=False))


def split_assignment(tok: Token) -> Optional[Tuple[str, Token]]:
    """If the word looks like NAME=value, return (NAME, token for the value)."""
    if tok.kind != WORD or not tok.parts or tok.parts[0][1] != "plain":
        return None
    m = ASSIGN_RE.fullmatch(tok.parts[0][0])
    if not m:
        return None
    rest = m.group(2)
    parts = ([(rest, "plain")] if rest else []) + tok.parts[1:]
    return m.group(1), Token(WORD, parts)
