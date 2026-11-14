"""The prompt: PS1 escapes and the default prompt."""
import getpass
import os
import socket
import time
from typing import List, Optional

from .state import ShellState


# Prompt
#
# Set PS1 in ~/.myshellrc to change it. These escapes work, as in bash:
#   \w  current directory, with ~ for home     \W  last part of the directory
#   \u  user name     \h  host name     \$  $ or # for root     \j  running jobs
#   \n  newline       \e  escape character      \\  backslash
#   \[ \]  wrap colors so line editing counts the width correctly
# One extra escape: \?  is the exit status of the last command.
# Time and git:
#   \t  time as 24-hour HH:MM:SS     \T  time as 12-hour HH:MM:SS     \A  time as HH:MM
#   \@  time as 12-hour am/pm        \d  date, such as "Wed Nov 12"
#   \g  git branch of the current folder, empty outside a repository
# The default prompt shows the branch after the folder.
DEFAULT_PS1_COLOR = r"\[\e[1;32m\]\w\[\e[0m\] \$ "
DEFAULT_PS1_PLAIN = r"\w \$ "
FAILED_PREFIX_COLOR = r"\[\e[1;31m\][\?]\[\e[0m\] "
FAILED_PREFIX_PLAIN = r"[\?] "


def current_dir() -> str:
    try:
        return os.getcwd()
    except OSError:
        return os.environ.get("PWD", "?")  # the directory was deleted


def now() -> time.struct_time:
    """The current local time. Tests replace this function."""
    return time.localtime()


def git_branch(start: Optional[str] = None) -> str:
    """The branch of the git repository around `start`, or "" outside one. A detached head
    shows the first seven characters of the commit. Reads .git directly, so git need not exist."""
    path = start or current_dir()
    for _ in range(64):
        dot_git = os.path.join(path, ".git")
        if os.path.isdir(dot_git):
            git_dir = dot_git
        elif os.path.isfile(dot_git):
            try:
                with open(dot_git, encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
            except OSError:
                return ""
            if not text.startswith("gitdir:"):
                return ""
            git_dir = os.path.join(path, text[len("gitdir:"):].strip())
        else:
            parent = os.path.dirname(path)
            if parent == path:
                return ""
            path = parent
            continue
        try:
            with open(os.path.join(git_dir, "HEAD"), encoding="utf-8", errors="replace") as f:
                head = f.read().strip()
        except OSError:
            return ""
        if head.startswith("ref:"):
            ref = head[4:].strip()
            return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        return head[:7]
    return ""


def format_duration(seconds: float) -> str:
    """2.3s, 45s, 3m 05s, 1h 02m."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def slow_notice(state: ShellState) -> str:
    """The text printed after a command that ran longer than SLOW_COMMAND_SECS
    (default 2 seconds). Set the variable to 0 or off to turn it off."""
    raw = state.get_variable("SLOW_COMMAND_SECS")
    if raw is None:
        limit = 2.0
    else:
        try:
            limit = float(raw)
        except ValueError:
            return ""
    if limit <= 0 or state.last_duration < limit:
        return ""
    return f"took {format_duration(state.last_duration)}"


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
        elif esc in "tTA@d":
            t = now()
            out.append({"t": time.strftime("%H:%M:%S", t), "T": time.strftime("%I:%M:%S", t),
                        "A": time.strftime("%H:%M", t), "@": time.strftime("%I:%M %p", t),
                        "d": time.strftime("%a %b %d", t)}[esc])
        elif esc == "g":
            out.append(git_branch())
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
    branch = git_branch()
    if branch:
        # The branch name goes in as plain text. A backslash in it must not act as an escape.
        safe = branch.replace("\\", "\\\\")
        shown = (r"\[\e[35m\](" + safe + r")\[\e[0m\]") if color else "(" + safe + ")"
        template = template.replace(r" \$ ", " " + shown + r" \$ ")
    if state.last_status != 0:
        template = (FAILED_PREFIX_COLOR if color else FAILED_PREFIX_PLAIN) + template
    return expand_prompt(template, state)
