"""Optional line editor built on prompt_toolkit 3.x: Ctrl+R search, history suggestions,
syntax colors and Tab completion. Without prompt_toolkit, make_reader() returns None and
the shell keeps using input() with readline. prompt_toolkit is imported lazily."""
import importlib
import os
import shutil
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import history
from .completion import complete_line
from .highlight import STYLES, tokenize_for_color
from .state import ShellState

SUGGESTION_STYLE = "fg:#888888"
_CACHE_LIMIT = 4000


def strip_markers(prompt: str) -> str:
    """Drop the readline markers \\x01 and \\x02 that wrap raw color codes."""
    return prompt.replace("\x01", "").replace("\x02", "")


def make_kind_of(state: ShellState) -> Callable[[str], str]:
    """A function word -> "command" or "unknown", with PATH lookups cached by name and PATH."""
    cache: Dict[Tuple[str, str], bool] = {}

    def kind_of(word: str) -> str:
        from .builtins import find_builtin
        functions = getattr(state, "functions", None) or {}
        if find_builtin(word) or word in state.aliases or word in functions:
            return "command"
        key = (word, os.environ.get("PATH", ""))
        found = cache.get(key)
        if found is None:
            if len(cache) >= _CACHE_LIMIT:
                cache.clear()
            try:
                found = bool(shutil.which(word))
            except Exception:
                found = False
            cache[key] = found
        return "command" if found else "unknown"

    return kind_of


def _wanted(force: bool) -> bool:
    mode = os.environ.get("MYSHELL_EDITOR", "").strip().lower()
    if mode == "basic":
        return False
    if force or mode == "fancy":
        return True
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except (AttributeError, ValueError):
        return False
    return os.environ.get("TERM", "") not in ("", "dumb")


def _build(state: ShellState, input: Any, output: Any) -> Any:
    """Create the PromptSession and its helpers. Returns (session, history_obj)."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.application import get_app
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import History
    from prompt_toolkit.lexers import Lexer
    from prompt_toolkit.shortcuts import CompleteStyle
    from prompt_toolkit.styles import Style

    class ShellHistory(History):
        """The shell's own history list, newest first. Lines are added by the shell."""

        def __init__(self) -> None:
            super().__init__()
            self.suspended = False  # true while reading continuation lines

        async def load(self):  # type: ignore[override]
            self._loaded = False  # re-read the shell's list on every prompt
            async for item in super().load():
                yield item

        def refresh(self) -> None:
            """Reload now, so suggestions see the newest lines before the first key."""
            self._loaded_strings = list(self.load_history_strings())
            self._loaded = True

        def load_history_strings(self):  # type: ignore[override]
            if self.suspended:
                return iter(())
            return iter(list(reversed(history.entries())))

        def append_string(self, string: str) -> None:
            if not self.suspended:
                super().append_string(string)

        def store_string(self, string: str) -> None:
            pass

    class ShellLexer(Lexer):
        def __init__(self) -> None:
            self.kind_of = make_kind_of(state)

        def lex_document(self, document):  # type: ignore[override]
            lines: List[List[Tuple[str, str]]] = [[]]
            for cls, text in tokenize_for_color(document.text, self.kind_of):
                style = "class:" + cls
                for n, part in enumerate(text.split("\n")):
                    if n:
                        lines.append([])
                    if part:
                        lines[-1].append((style, part))

            def get_line(lineno: int):
                return lines[lineno] if 0 <= lineno < len(lines) else []
            return get_line

    class ShellCompleter(Completer):
        def get_completions(self, document, complete_event):  # type: ignore[override]
            before = document.text_before_cursor
            start, found = complete_line(state, before)
            for candidate in found:
                yield Completion(candidate, start_position=start - len(before))

    shell_history = ShellHistory()
    suggester = AutoSuggestFromHistory()

    # prompt_toolkit computes suggestions in a background task. When keys arrive faster
    # than that task runs (fast typing, pasted text), Right/End would find none. These
    # bindings compute the suggestion on the spot instead.
    bindings = KeyBindings()

    @Condition
    def at_end_without_suggestion() -> bool:
        buf = get_app().current_buffer
        return (buf.suggestion is None and buf.document.is_cursor_at_the_end
                and bool(buf.text))

    def accept_now(event) -> None:
        buf = event.current_buffer
        found = suggester.get_suggestion(buf, buf.document)
        if found:
            buf.insert_text(found.text)

    bindings.add("right", filter=at_end_without_suggestion)(accept_now)
    bindings.add("end", filter=at_end_without_suggestion)(accept_now)
    style = Style.from_dict(dict(STYLES, **{"auto-suggestion": SUGGESTION_STYLE}))
    kwargs: Dict[str, Any] = {}
    if input is not None:
        kwargs["input"] = input
    if output is not None:
        kwargs["output"] = output
    session = PromptSession(
        history=shell_history,
        auto_suggest=suggester,
        key_bindings=bindings,
        lexer=ShellLexer(),
        completer=ShellCompleter(),
        complete_while_typing=False,
        complete_style=CompleteStyle.READLINE_LIKE,
        enable_history_search=False,
        mouse_support=False,
        style=style,
        **kwargs)
    return session, shell_history


class LineReader:
    """input()-like reader over one PromptSession. Attributes `session`, `history`,
    `lexer`, `completer` are exposed for tests."""

    def __init__(self, state: ShellState, input: Any = None, output: Any = None):
        self.state = state
        self.session, self.history = _build(state, input, output)
        self.lexer = self.session.lexer
        self.completer = self.session.completer

    def read(self, prompt: str = "") -> str:
        """Like input(): raises EOFError on Ctrl+D at an empty line, KeyboardInterrupt on Ctrl+C."""
        from prompt_toolkit.formatted_text import ANSI
        self.history.refresh()
        return self.session.prompt(ANSI(strip_markers(prompt)))

    def read_continuation(self, prompt: str = "> ") -> str:
        """Read an extra line (a here-document body or a continued command): no
        suggestions, and the line is not added to the history."""
        from prompt_toolkit.auto_suggest import DummyAutoSuggest
        from prompt_toolkit.formatted_text import ANSI
        suggest = self.session.auto_suggest
        self.history.suspended = True
        self.session.auto_suggest = DummyAutoSuggest()
        try:
            return self.session.prompt(ANSI(strip_markers(prompt)))
        finally:
            self.history.suspended = False
            self.session.auto_suggest = suggest


def make_reader(state: ShellState, input: Any = None, output: Any = None,
                force: bool = False) -> Optional[LineReader]:
    """A LineReader, or None when the basic input()/readline path should be used:
    prompt_toolkit missing, MYSHELL_EDITOR=basic, stdin or stdout not a terminal, or TERM
    empty or dumb. MYSHELL_EDITOR=fancy or force=True skips the terminal checks."""
    if not _wanted(force):
        return None
    try:
        importlib.import_module("prompt_toolkit")
    except ImportError:
        return None
    history.use_builtin()
    try:
        return LineReader(state, input, output)
    except Exception:
        return None
