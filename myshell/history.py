"""Command history that works with or without a full readline.

On Linux and macOS the readline module keeps the history, and input() records each
line itself. On Windows the readline module comes from pyreadline3 and lacks several
functions, or is missing. There the shell keeps its own list and file.

Code elsewhere reads `native` when it runs, so tests can switch modes."""
import os
from typing import List, Optional

try:
    import readline as _rl
except ImportError:  # no readline at all
    _rl = None

MAX_ENTRIES = 1000
_NEEDED = ("get_current_history_length", "get_history_item", "add_history",
           "clear_history", "remove_history_item", "read_history_file",
           "write_history_file", "set_history_length")

# True when readline can do everything, and the platform is not Windows.
native = (_rl is not None and os.name != "nt"
          and all(hasattr(_rl, name) for name in _NEEDED))

_entries: List[str] = []  # used when native is False


def use_builtin() -> None:
    """Switch to the shell's own list, so a line editor other than readline can use it."""
    global native
    if native:  # keep what readline already holds
        _entries[:] = [_rl.get_history_item(i)
                       for i in range(1, _rl.get_current_history_length() + 1)][-MAX_ENTRIES:]
    native = False


def entries() -> List[str]:
    """All entries, oldest first."""
    if native:
        return [_rl.get_history_item(i) for i in range(1, _rl.get_current_history_length() + 1)]
    return list(_entries)


def records_input() -> bool:
    """True when input() adds each typed line to the history by itself."""
    return native


def add(line: str) -> None:
    if native:
        _rl.add_history(line)
    else:
        _entries.append(line)
        del _entries[:-MAX_ENTRIES]


def length() -> int:
    return _rl.get_current_history_length() if native else len(_entries)


def item(position: int) -> str:
    """The entry at a 1-based position."""
    return _rl.get_history_item(position) if native else _entries[position - 1]


def clear() -> None:
    if native:
        _rl.clear_history()
    else:
        _entries.clear()


def remove(position: int) -> None:
    """Remove the entry at a 1-based position."""
    if native:
        _rl.remove_history_item(position - 1)
    else:
        del _entries[position - 1]


def forget_last() -> None:
    """Drop the newest entry. The guide uses it to keep menu answers out of the history."""
    if length():
        remove(length())


def write(path: str) -> None:
    if native:
        _rl.write_history_file(path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(line.replace("\n", " ") + "\n" for line in _entries)


def read(path: str) -> None:
    """Add the entries of a history file to the current history."""
    if native:
        _rl.read_history_file(path)
    else:
        with open(path, encoding="utf-8") as f:
            for line in f.read().splitlines():
                if line:
                    add(line)


def set_limit(limit: int = MAX_ENTRIES) -> None:
    if native:
        _rl.set_history_length(limit)
    else:
        del _entries[:-limit]


def readline_module() -> Optional[object]:
    """The readline module for completion, or None."""
    return _rl
