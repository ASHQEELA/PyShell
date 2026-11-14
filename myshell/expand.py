"""Expansion: variables, $(...), word splitting, globbing and tilde."""
import glob
import os
import re
import sys
from typing import List, Optional, Tuple

from . import compat
from .arith import evaluate
from .errors import ShellError, ShellExit
from .lexer import WORD, Token, find_matching_brace, find_matching_paren, read_backticks
from .state import ShellState


VAR_RE = re.compile(r"\$(\?|\$|#|@|\*|[0-9]|[A-Za-z_][A-Za-z0-9_]*)")
PARAM_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*|[0-9]+|[?$#@*])(.*)", re.DOTALL)
SPECIAL_NAMES = ("?", "$", "#", "@", "*")
ASSIGN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)


def portable_substitution(text: str, state: ShellState) -> str:
    """No fork here (Windows). Run the text in this process with output sent to a temp file.
    The folder, the variables and the environment are restored afterwards."""
    import tempfile
    sys.stdout.flush()
    sys.stderr.flush()
    saved_out = sys.stdout
    saved_cwd = os.getcwd()
    saved_env = dict(os.environ)
    saved_vars = dict(state.variables)
    saved_pos = list(state.positional)
    saved_status = state.last_status
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as f:
        sys.stdout = f
        try:
            state.execute(text)
        except ShellExit:
            pass
        except ShellError as e:
            print(e, file=sys.stderr)
        finally:
            f.flush()
            sys.stdout = saved_out
            os.chdir(saved_cwd)
            os.environ.clear()
            os.environ.update(saved_env)
            state.variables.clear()
            state.variables.update(saved_vars)
            state.positional = saved_pos
        f.seek(0)
        output = f.read()
    state.last_status = saved_status
    return output.rstrip("\n")


def command_substitution(text: str, state: ShellState) -> str:
    """Run text in a subshell and return its output without trailing newlines.
    The subshell is a forked copy of this shell, so aliases, builtins and
    variables work. Changes it makes (cd, exports) do not reach the parent."""
    if not compat.HAS_FORK:
        return portable_substitution(text, state)
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


def fail(state: ShellState, message: str) -> ShellError:
    """An expansion error. A script stops at it, as in other shells. The prompt carries on."""
    if not state.jobs.interactive and not state.keep_going:
        print(message, file=sys.stderr)
        raise ShellExit(1)
    return ShellError(message)


def lookup(name: str, state: ShellState) -> Optional[str]:
    """The value of a variable or special parameter. None when it is not set."""
    if name == "?":
        return str(state.last_status)
    if name == "$":
        return str(os.getpid())
    if name == "#":
        return str(len(state.positional))
    if name in ("@", "*"):
        return " ".join(state.positional)
    if name.isdigit():
        n = int(name)
        if n == 0:
            return state.script_name
        return state.positional[n - 1] if n <= len(state.positional) else None
    return state.get_variable(name)


def variable_value(name: str, state: ShellState) -> str:
    value = lookup(name, state)
    if value is None:
        if "nounset" in state.options and name not in SPECIAL_NAMES and not name.isdigit():
            raise fail(state, f"{name}: unbound variable")
        return ""
    return value


# --- glob patterns used by ${X#pat}, ${X/pat/new} and case ---
def glob_to_regex(pattern: str) -> str:
    out = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        i += 1
        if c == "*":
            out.append(".*")
        elif c == "?":
            out.append(".")
        elif c == "[":
            j = i
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(c))
            else:
                body = pattern[i:j]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
    return "".join(out)


def glob_match(pattern: str, text: str) -> bool:
    return re.fullmatch(glob_to_regex(pattern), text, re.DOTALL) is not None


def expand_param_word(text: str, state: ShellState) -> str:
    """Expand the word inside ${X:-word}: quotes, backslashes and $ expansions. No splitting."""
    out: List[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "'":
            end = text.find("'", i + 1)
            end = len(text) if end < 0 else end
            out.append(text[i + 1:end])
            i = end + 1
        elif c == '"':
            end = i + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(expand_vars(text[i + 1:end], state))
            i = end + 1
        elif c == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        elif c == "$":
            found = expand_dollar(text, i, state)
            if found is None:
                out.append(c)
                i += 1
            else:
                out.append(found[0])
                i = found[1]
        else:
            out.append(c)
            i += 1
    return "".join(out)


def expand_param_pattern(text: str, state: ShellState) -> str:
    """Like expand_param_word, but quoted characters stay literal inside a glob pattern."""
    out: List[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "'":
            end = text.find("'", i + 1)
            end = len(text) if end < 0 else end
            out.append(glob.escape(text[i + 1:end]))
            i = end + 1
        elif c == '"':
            end = i + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(glob.escape(expand_vars(text[i + 1:end], state)))
            i = end + 1
        elif c == "\\" and i + 1 < len(text):
            out.append(glob.escape(text[i + 1]))
            i += 2
        elif c == "$":
            found = expand_dollar(text, i, state)
            if found is None:
                out.append(c)
                i += 1
            else:
                out.append(found[0])
                i = found[1]
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_unescaped(text: str, sep: str) -> Tuple[str, Optional[str]]:
    """Split text at the first sep that is not escaped or inside quotes or ${}."""
    depth = 0
    quote = None
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "$" and text.startswith("${", i):
            depth += 1
            i += 1
        elif c == "}" and depth:
            depth -= 1
        elif c == sep and depth == 0:
            return text[:i], text[i + 1:]
        i += 1
    return text, None


def param_expansion(body: str, state: ShellState) -> str:
    """The text inside ${...}: ${X}, ${#X}, ${X:-w}, ${X:=w}, ${X:?w}, ${X:+w},
    ${X#p} ${X##p} ${X%p} ${X%%p}, ${X/p/r} ${X//p/r}, ${X:off:len}, ${X^^} ${X,,}."""
    if body == "#":
        return str(len(state.positional))
    if body.startswith("#") and len(body) > 1 and PARAM_RE.fullmatch(body[1:]):
        name = body[1:]
        if name in ("@", "*"):
            return str(len(state.positional))
        return str(len(variable_value(name, state)))
    m = PARAM_RE.fullmatch(body)
    if not m:
        raise ShellError(f"${{{body}}}: bad substitution")
    name, op = m.group(1), m.group(2)
    if not op:
        return variable_value(name, state)
    raw = lookup(name, state)
    value = raw if raw is not None else ""
    colon = op.startswith(":") and len(op) > 1 and op[1] in "-=?+"
    if colon or op[0] in "-=?+":
        kind = op[1] if colon else op[0]
        word = op[2:] if colon else op[1:]
        missing = raw is None or (colon and raw == "")
        if kind == "-":
            return expand_param_word(word, state) if missing else value
        if kind == "=":
            if not missing:
                return value
            if name in SPECIAL_NAMES or name.isdigit():
                raise ShellError(f"${name}: cannot assign in this way")
            new = expand_param_word(word, state)
            state.set_variable(name, new)
            return new
        if kind == "?":
            if missing:
                message = expand_param_word(word, state) or "parameter null or not set"
                raise fail(state, f"{name}: {message}")
            return value
        return "" if missing else expand_param_word(word, state)  # "+"
    if "nounset" in state.options and raw is None and name not in SPECIAL_NAMES:
        raise fail(state, f"{name}: unbound variable")
    if op[0] in "#%":
        longest = op.startswith(op[0] * 2)
        pattern = expand_param_pattern(op[2 if longest else 1:], state)
        if op[0] == "#":
            sizes = range(len(value), -1, -1) if longest else range(0, len(value) + 1)
            for k in sizes:
                if glob_match(pattern, value[:k]):
                    return value[k:]
        else:
            starts = range(0, len(value) + 1) if longest else range(len(value), -1, -1)
            for k in starts:
                if glob_match(pattern, value[k:]):
                    return value[:k]
        return value
    if op[0] == "/":
        mode = ""
        rest = op[1:]
        if rest[:1] in ("/", "#", "%"):
            mode, rest = rest[0], rest[1:]
        pat_text, rep_text = split_unescaped(rest, "/")
        pattern = expand_param_pattern(pat_text, state)
        replacement = expand_param_word(rep_text or "", state)
        if pattern == "":
            return value
        rx = glob_to_regex(pattern)
        if mode == "#":
            rx = "^" + rx
        elif mode == "%":
            rx = rx + "$"
        count = 0 if mode == "/" else 1
        return re.sub(rx, lambda _: replacement, value, count=count, flags=re.DOTALL)
    if op[0] == ":":
        off_text, len_text = split_unescaped(op[1:], ":")
        offset = int(arithmetic(off_text.strip() or "0", state))
        if offset < 0:
            offset = max(0, len(value) + offset)
        if len_text is None:
            return value[offset:]
        length = int(arithmetic(len_text.strip() or "0", state))
        return value[offset:offset + length] if length >= 0 else value[offset:length]
    if op in ("^^", "^", ",,", ","):
        if op == "^^":
            return value.upper()
        if op == ",,":
            return value.lower()
        return (value[:1].upper() if op == "^" else value[:1].lower()) + value[1:]
    raise ShellError(f"${{{body}}}: bad substitution")


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
    if text.startswith("${", i):
        end = find_matching_brace(text, i + 1)
        return param_expansion(text[i + 2:end], state), end + 1
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


# --- brace expansion: {a,b} and {1..5} ---
Cell = Tuple[str, str, bool]  # (text, mode, protected). Plain text is one character per cell.
RANGE_NUM = re.compile(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?")
RANGE_CHAR = re.compile(r"([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?")
MAX_BRACE_WORDS = 20000


def token_cells(tok: Token) -> List[Cell]:
    cells: List[Cell] = []
    for text, mode in tok.parts:
        if mode != "plain":
            cells.append((text, mode, True))
            continue
        protected_until = -1
        for i, ch in enumerate(text):
            if i > protected_until and ch == "$" and text[i + 1:i + 2] in ("{", "("):
                try:
                    if text[i + 1] == "{":
                        protected_until = find_matching_brace(text, i + 1)
                    else:
                        protected_until = find_matching_paren(text, i + 1)
                except ShellError:
                    protected_until = len(text)
            cells.append((ch, "plain", i <= protected_until))
    return cells


def cells_token(cells: List[Cell]) -> Token:
    parts: List[Tuple[str, str]] = []
    for text, mode, _ in cells:
        if parts and parts[-1][1] == mode == "plain":
            parts[-1] = (parts[-1][0] + text, mode)
        else:
            parts.append((text, mode))
    return Token(WORD, parts)


def is_brace(cell: Cell, ch: str) -> bool:
    return cell[1] == "plain" and not cell[2] and cell[0] == ch


def matching_brace(cells: List[Cell], start: int) -> Optional[int]:
    depth = 0
    for j in range(start, len(cells)):
        if is_brace(cells[j], "{"):
            depth += 1
        elif is_brace(cells[j], "}"):
            depth -= 1
            if depth == 0:
                return j
    return None


def split_commas(cells: List[Cell]) -> List[List[Cell]]:
    parts: List[List[Cell]] = [[]]
    depth = 0
    for c in cells:
        if is_brace(c, "{"):
            depth += 1
        elif is_brace(c, "}"):
            depth -= 1
        if depth == 0 and is_brace(c, ","):
            parts.append([])
        else:
            parts[-1].append(c)
    return parts


def range_values(inner: List[Cell]) -> Optional[List[str]]:
    if any(c[1] != "plain" for c in inner):
        return None
    text = "".join(c[0] for c in inner)
    m = RANGE_NUM.fullmatch(text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        step = abs(int(m.group(3))) if m.group(3) else 1
        if step == 0:
            return None
        width = 0
        for end in (m.group(1), m.group(2)):
            digits = end.lstrip("-")
            if len(digits) > 1 and digits.startswith("0"):
                width = max(width, len(end))
        values = range(a, b + 1, step) if a <= b else range(a, b - 1, -step)
        if len(values) > MAX_BRACE_WORDS:
            return None
        return [str(v).zfill(width) if width else str(v) for v in values]
    m = RANGE_CHAR.fullmatch(text)
    if m:
        a, b = ord(m.group(1)), ord(m.group(2))
        step = abs(int(m.group(3))) if m.group(3) else 1
        if step == 0:
            return None
        values = range(a, b + 1, step) if a <= b else range(a, b - 1, -step)
        return [chr(v) for v in values]
    return None


def expand_cells(cells: List[Cell]) -> List[List[Cell]]:
    for i, cell in enumerate(cells):
        if not is_brace(cell, "{"):
            continue
        j = matching_brace(cells, i)
        if j is None:
            continue
        inner = cells[i + 1:j]
        alternatives = split_commas(inner)
        if len(alternatives) > 1:
            pieces = alternatives
        else:
            values = range_values(inner)
            if values is None:
                continue
            pieces = [[(ch, "plain", False) for ch in v] for v in values]
        result: List[List[Cell]] = []
        for piece in pieces:
            result.extend(expand_cells(cells[:i] + piece + cells[j + 1:]))
            if len(result) > MAX_BRACE_WORDS:
                raise ShellError("brace expansion: too many words")
        return result
    return [cells]


def brace_expand(tok: Token) -> List[Token]:
    """{a,b,c} and {1..5} turn one word into several. Quoted braces stay literal."""
    if not any(mode == "plain" and "{" in text for text, mode in tok.parts):
        return [tok]
    return [cells_token(c) for c in expand_cells(token_cells(tok))]


def add_double_quoted(text: str, state: ShellState, fields: "_Fields") -> None:
    """Text inside double quotes. "$@" becomes one word per positional parameter."""
    pieces = re.split(r"(\$@|\$\{@\})", text)
    if len(pieces) == 1:
        t = expand_vars(text, state)
        fields.add(t, glob.escape(t), quoted=True)
        return
    for k, piece in enumerate(pieces):
        if k % 2 == 0:
            t = expand_vars(piece, state)
            fields.add(t, glob.escape(t), quoted=bool(t))
        else:
            for j, value in enumerate(state.positional):
                if j:
                    fields.end_field()
                fields.add(value, glob.escape(value), quoted=True)


def expand_word(tok: Token, state: ShellState, globbing: bool = True,
                splitting: bool = True, braces: bool = True) -> List[str]:
    """Expand one word token into zero or more strings.
    Braces expand first. Unquoted $VAR and $(...) results split on whitespace and may
    match files. Quoted text and literal text never split."""
    if braces:
        words = brace_expand(tok)
        if len(words) != 1 or words[0] is not tok:
            result: List[str] = []
            for w in words:
                result.extend(expand_word(w, state, globbing, splitting, braces=False))
            return result
    fields = _Fields()
    for idx, (text, mode) in enumerate(tok.parts):
        if mode == "single":
            fields.add(text, glob.escape(text), quoted=True)
        elif mode == "double":
            add_double_quoted(text, state, fields)
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
    return "".join(expand_word(tok, state, globbing=False, splitting=False, braces=False))


def expand_pattern(tok: Token, state: ShellState) -> str:
    """Expand a case pattern. Quoted characters are literal, the rest may be wildcards."""
    out: List[str] = []
    for idx, (text, mode) in enumerate(tok.parts):
        if mode == "single":
            out.append(glob.escape(text))
        elif mode == "double":
            out.append(glob.escape(expand_vars(text, state)))
        else:
            if idx == 0:
                text = os.path.expanduser(text)
            out.append("".join(seg for seg, _ in expand_segments(text, state)))
    return "".join(out)


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
