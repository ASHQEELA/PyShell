"""The prompt: PS1 escapes and the default prompt."""
import getpass
import os
import socket
from typing import List

from .state import ShellState


# Prompt
#
# Set PS1 in ~/.myshellrc to change it. These escapes work, as in bash:
#   \w  current directory, with ~ for home     \W  last part of the directory
#   \u  user name     \h  host name     \$  $ or # for root     \j  running jobs
#   \n  newline       \e  escape character      \\  backslash
#   \[ \]  wrap colors so line editing counts the width correctly
# One extra escape: \?  is the exit status of the last command.
DEFAULT_PS1_COLOR = r"\[\e[1;32m\]\w\[\e[0m\] \$ "
DEFAULT_PS1_PLAIN = r"\w \$ "
FAILED_PREFIX_COLOR = r"\[\e[1;31m\][\?]\[\e[0m\] "
FAILED_PREFIX_PLAIN = r"[\?] "


def current_dir() -> str:
    try:
        return os.getcwd()
    except OSError:
        return os.environ.get("PWD", "?")  # the directory was deleted


def short_path(path: str) -> str:
    home = os.environ.get("HOME", "").rstrip("/")
    if home:
        if path == home:
            return "~"
        if path.startswith(home + "/"):
            return "~" + path[len(home):]
    return path


def expand_prompt(template: str, state: ShellState) -> str:
    out: List[str] = []
    i = 0
    while i < len(template):
        c = template[i]
        if c != "\\" or i + 1 >= len(template):
            out.append(c)
            i += 1
            continue
        esc = template[i + 1]
        i += 2
        if esc == "w":
            out.append(short_path(current_dir()))
        elif esc == "W":
            path = current_dir()
            out.append("~" if short_path(path) == "~" else (os.path.basename(path) or "/"))
        elif esc == "u":
            try:
                out.append(getpass.getuser())
            except Exception:
                out.append("user")
        elif esc == "h":
            out.append(socket.gethostname().split(".")[0])
        elif esc == "$":
            out.append("#" if hasattr(os, "geteuid") and os.geteuid() == 0 else "$")
        elif esc == "j":
            out.append(str(sum(1 for j in state.jobs.numbered() if not j.completed)))
        elif esc == "?":
            out.append(str(state.last_status))
        elif esc == "n":
            out.append("\n")
        elif esc == "e":
            out.append("\x1b")
        elif esc == "[":
            out.append("\x01")  # readline: the text up to \] takes no screen width
        elif esc == "]":
            out.append("\x02")
        elif esc == "\\":
            out.append("\\")
        else:
            out.append("\\" + esc)
    return "".join(out)


def build_continuation_prompt(state: ShellState) -> str:
    """The prompt for extra input lines, such as a here-document body. Set PS2 to change it."""
    custom = state.get_variable("PS2")
    return expand_prompt(custom if custom is not None else "> ", state)


def build_prompt(state: ShellState) -> str:
    custom = state.get_variable("PS1")
    if custom is not None:
        return expand_prompt(custom, state)
    color = "NO_COLOR" not in os.environ and os.environ.get("TERM", "") not in ("", "dumb")
    template = DEFAULT_PS1_COLOR if color else DEFAULT_PS1_PLAIN
    if state.last_status != 0:
        template = (FAILED_PREFIX_COLOR if color else FAILED_PREFIX_PLAIN) + template
    return expand_prompt(template, state)
