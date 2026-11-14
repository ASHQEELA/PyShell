"""Tab completion for commands, paths and variables."""
import os
import re
from typing import List, Optional, Tuple

from . import history
from .builtins import builtin_names
from .state import ShellState


ESCAPE_CHARS = " \t\n\\'\"$&;|<>*?[](){}#`!"
COMMAND_PREFIXES = ("|", "&&", "||", ";", "&")


def escape_word(text: str) -> str:
    return "".join("\\" + c if c in ESCAPE_CHARS else c for c in text)


def unescape_word(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


readline = history.readline_module()  # None when there is no readline


class Completer:
    """Completes command names, file paths and $variables for readline."""

    def __init__(self, state: ShellState):
        self.state = state
        self.matches: List[str] = []

    def __call__(self, text: str, index: int) -> Optional[str]:
        if index == 0:
            self.matches = self.candidates(text)
        return self.matches[index] if index < len(self.matches) else None

    def candidates(self, text: str) -> List[str]:
        before = readline.get_line_buffer()[:readline.get_begidx()]
        return self.candidates_for(text, before)

    def candidates_for(self, text: str, before: str) -> List[str]:
        """Matches for the word `text`, which follows the line part `before`."""
        if text.startswith("$"):
            return self.variable_matches(text)
        before = before.rstrip()
        at_command = (before == "" or (before.endswith(COMMAND_PREFIXES)
                                       and not before.endswith(">")))
        if at_command and "/" not in text and not text.startswith("~"):
            return self.command_matches(text)
        return self.path_matches(text)

    def variable_matches(self, text: str) -> List[str]:
        names = set(os.environ) | set(self.state.variables)
        return sorted("$" + n for n in names if ("$" + n).startswith(text))

    def command_matches(self, text: str) -> List[str]:
        functions = getattr(self.state, "functions", None) or {}
        names = {n for n in builtin_names() + list(self.state.aliases) + list(functions)
                 if n.startswith(text)}
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            try:
                entries = os.listdir(directory or ".")
            except OSError:
                continue
            for entry in entries:
                if entry.startswith(text) and entry not in names:
                    path = os.path.join(directory, entry)
                    if os.path.isfile(path) and os.access(path, os.X_OK):
                        names.add(entry)
        return sorted(names)

    def path_matches(self, text: str) -> List[str]:
        raw = unescape_word(text)
        if raw == "~":
            return ["~/"]
        base = os.path.basename(raw)
        prefix = raw[:len(raw) - len(base)]
        directory = os.path.expanduser(prefix) if prefix else "."
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            return []
        result = []
        for name in entries:
            if not name.startswith(base):
                continue
            if name.startswith(".") and not base.startswith("."):
                continue  # hide dotfiles unless asked
            suffix = "/" if os.path.isdir(os.path.join(directory, name)) else ""
            result.append(escape_word(prefix + name).replace("\\~", "~") + suffix)
        return result


WORD_DELIMS = " \t\n;|&<>"


def word_start(line: str) -> int:
    """Index where the word at the end of `line` begins. A backslash keeps the next
    character in the word, so "my\\ fi" is one word."""
    start = 0
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\":
            i += 2
            continue
        i += 1
        if c in WORD_DELIMS:
            start = i
    return min(start, len(line))


def complete_line(state: ShellState, line_before_cursor: str) -> Tuple[int, List[str]]:
    """Completions for the word ending at the cursor, without readline.
    Returns (index where that word starts, candidates that replace it)."""
    start = word_start(line_before_cursor)
    text = line_before_cursor[start:]
    try:
        found = Completer(state).candidates_for(text, line_before_cursor[:start])
    except Exception:
        found = []
    return start, found


def setup_readline(state: ShellState) -> None:
    if readline is None:
        return
    try:
        readline.set_completer_delims(" \t\n;|&<>")
        readline.set_completer(Completer(state))
        if "libedit" in (readline.__doc__ or ""):  # macOS
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
    except Exception:
        pass
