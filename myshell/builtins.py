"""Builtin commands. Each takes (state, args, stdout, stderr) and returns a status."""
import os
import re
import shutil
from typing import Callable, Dict, List, Optional, TextIO

from .paths import expand_user
from . import compat, history
from .coreutils import PORTABLE
from .more_builtins import EXTRA_BUILTINS, unescape
from .errors import BreakLoop, ContinueLoop, ReturnFromFunction, ShellError, ShellExit
from .state import ShellState


# Signature: (state, args, stdout, stderr) -> exit status
Builtin = Callable[[ShellState, List[str], TextIO, TextIO], int]


def builtin_echo(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    newline, escapes = True, False
    while args and re.fullmatch(r"-[neE]+", args[0]):
        newline = newline and "n" not in args[0]
        if "e" in args[0]:
            escapes = True
        if "E" in args[0]:
            escapes = False
        args = args[1:]
    text = " ".join(args)
    stdout.write((unescape(text) if escapes else text) + ("\n" if newline else ""))
    return 0


def builtin_pwd(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if args:
        print("pwd: too many arguments", file=stderr)
        return 1
    print(os.getcwd(), file=stdout)
    return 0


def builtin_cd(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if len(args) > 1:
        print("cd: too many arguments", file=stderr)
        return 1
    if not args:
        target = os.environ.get("HOME", "")
        if not target:
            print("cd: HOME not set", file=stderr)
            return 1
    elif args[0] == "-":
        target = os.environ.get("OLDPWD", "")
        if not target:
            print("cd: OLDPWD not set", file=stderr)
            return 1
    else:
        target = args[0]
    try:
        old = os.getcwd()
    except OSError:
        old = os.environ.get("PWD", "")
    try:
        os.chdir(expand_user(target))
    except OSError as e:
        print(f"cd: {target}: {e.strerror}", file=stderr)
        return 1
    os.environ["OLDPWD"] = old
    os.environ["PWD"] = os.getcwd()
    if args and args[0] == "-":
        print(os.getcwd(), file=stdout)
    return 0


def builtin_exit(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if len(args) > 1:
        print("exit: too many arguments", file=stderr)
        return 1
    code = state.last_status
    if args:
        try:
            code = int(args[0]) & 0xFF
        except ValueError:
            print(f"exit: {args[0]}: numeric argument required", file=stderr)
            code = 2
    raise ShellExit(code)


def builtin_alias(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not args:
        for k, v in state.aliases.items():
            print(f"alias {k}='{v}'", file=stdout)
        return 0
    status = 0
    for arg in args:
        if "=" in arg:
            k, v = arg.split("=", 1)
            state.aliases[k] = v
        elif arg in state.aliases:
            print(f"alias {arg}='{state.aliases[arg]}'", file=stdout)
        else:
            print(f"alias: {arg}: not found", file=stderr)
            status = 1
    return status


def find_builtin(name: str) -> Optional[Builtin]:
    """A builtin by name. The portable commands (ls, cat, ...) only count when the system
    has no program of that name, or when MYSHELL_PORTABLE=1 asks for them."""
    func = BUILTINS.get(name)
    if func is not None:
        return func
    func = PORTABLE.get(name)
    if func is not None and (os.environ.get("MYSHELL_PORTABLE") == "1"
                             or shutil.which(name) is None):
        return func
    return None


def builtin_names() -> List[str]:
    return sorted(set(BUILTINS) | {n for n in PORTABLE if find_builtin(n)})


def describe(state: ShellState, name: str) -> Optional[str]:
    """How the shell would run name: an alias, a function, a builtin or a program."""
    if name in state.aliases:
        return f"{name} is an alias for '{state.aliases[name]}'"
    if name in state.functions:
        return f"{name} is a shell function"
    if find_builtin(name):
        return f"{name} is a shell builtin"
    exe = shutil.which(name)
    return f"{name} is {exe}" if exe else None


def builtin_type(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not args:
        print("type: missing argument", file=stderr)
        return 1
    text = describe(state, args[0])
    if text is None:
        print(f"{args[0]}: not found", file=stderr)
        return 1
    print(text, file=stdout)
    return 0


def builtin_jobs(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not compat.HAS_FORK:
        return 0
    jc = state.jobs
    jc.refresh()
    for job in jc.numbered():
        print(jc.format(job), file=stdout)
    for job in [j for j in jc.numbered() if j.completed]:
        jc.remove(job)  # a finished job is listed once
    return 0


def _job_builtin_ready(name: str, stderr: TextIO) -> bool:
    if compat.HAS_FORK:
        return True
    print(f"{name}: job control is not supported on this platform", file=stderr)
    return False


def builtin_fg(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not _job_builtin_ready("fg", stderr):
        return 1
    jc = state.jobs
    jc.refresh()
    try:
        job = jc.find(args[0] if args else None)
    except ShellError as e:
        print(f"fg: {e}", file=stderr)
        return 1
    if job.completed:
        jc.remove(job)
        print("fg: job has terminated", file=stderr)
        return 1
    print(job.command, file=stdout)
    stdout.flush()
    return jc.put_in_foreground(job, cont=True)


def builtin_bg(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not _job_builtin_ready("bg", stderr):
        return 1
    jc = state.jobs
    jc.refresh()
    try:
        job = jc.find(args[0] if args else None)
    except ShellError as e:
        print(f"bg: {e}", file=stderr)
        return 1
    if job.completed:
        jc.remove(job)
        print("bg: job has terminated", file=stderr)
        return 1
    if not job.stopped:
        print(f"bg: job {job.number} already in background", file=stderr)
        return 0
    jc.put_in_background(job, cont=True)
    return 0


def builtin_wait(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not _job_builtin_ready("wait", stderr):
        return 1
    jc = state.jobs
    jc.refresh()
    try:
        targets = [jc.find(a) for a in args] if args else \
                  [j for j in jc.numbered() if not j.completed]
    except ShellError as e:
        print(f"wait: {e}", file=stderr)
        return 127
    status = 0
    for job in targets:
        if jc.wait_for(job, interruptible=True):
            print()
            return 130
        if job.completed:
            status = job.last_status if args else 0
            jc.remove(job)
    return status


NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def builtin_export(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not args:
        for k in sorted(os.environ):
            v = os.environ[k].replace("\\", "\\\\").replace('"', '\\"')
            print(f'declare -x {k}="{v}"', file=stdout)
        return 0
    status = 0
    for arg in args:
        name, sep, value = arg.partition("=")
        if not NAME_RE.fullmatch(name):
            print(f"export: `{arg}': not a valid identifier", file=stderr)
            status = 1
        elif sep:
            state.export_variable(name, value)
        else:
            state.export_variable(name)
    return status


def builtin_unset(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    status = 0
    functions = False
    if args and args[0] in ("-f", "-v"):
        functions, args = args[0] == "-f", args[1:]
    for name in args:
        if functions:
            state.functions.pop(name, None)
        elif NAME_RE.fullmatch(name):
            state.unset_variable(name)
        else:
            print(f"unset: `{name}': not a valid identifier", file=stderr)
            status = 1
    return status


FILE_TESTS = {
    "-e": os.path.exists, "-f": os.path.isfile, "-d": os.path.isdir,
    "-L": os.path.islink, "-h": os.path.islink,
    "-r": lambda p: os.access(p, os.R_OK), "-w": lambda p: os.access(p, os.W_OK),
    "-x": lambda p: os.access(p, os.X_OK),
    "-s": lambda p: os.path.exists(p) and os.path.getsize(p) > 0,
}
INT_TESTS = {
    "-eq": lambda a, b: a == b, "-ne": lambda a, b: a != b, "-lt": lambda a, b: a < b,
    "-le": lambda a, b: a <= b, "-gt": lambda a, b: a > b, "-ge": lambda a, b: a >= b,
}


def evaluate_test(args: List[str]) -> bool:
    n = len(args)
    if n == 0:
        return False
    if n >= 2 and args[0] == "!":
        return not evaluate_test(args[1:])
    if n == 1:
        return args[0] != ""
    if n == 2:
        op, value = args
        if op == "-z":
            return value == ""
        if op == "-n":
            return value != ""
        if op in FILE_TESTS:
            return bool(FILE_TESTS[op](value))
        raise ShellError(f"{op}: unary operator expected")
    if n == 3:
        a, op, b = args
        if op in ("=", "=="):
            return a == b
        if op == "!=":
            return a != b
        if op in INT_TESTS:
            try:
                return INT_TESTS[op](int(a), int(b))
            except ValueError:
                raise ShellError("integer expression expected")
        raise ShellError(f"{op}: binary operator expected")
    raise ShellError("too many arguments")


def run_test(name: str, args: List[str], stderr: TextIO) -> int:
    """Status 0 when true, 1 when false, 2 on a bad expression."""
    try:
        return 0 if evaluate_test(args) else 1
    except ShellError as e:
        print(f"{name}: {e}", file=stderr)
        return 2


def builtin_test(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    return run_test("test", args, stderr)


def builtin_bracket(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not args or args[-1] != "]":
        print("[: missing `]'", file=stderr)
        return 2
    return run_test("[", args[:-1], stderr)


def loop_control(name: str, exc, state: ShellState, args: List[str], stderr: TextIO) -> int:
    if len(args) > 1:
        print(f"{name}: too many arguments", file=stderr)
        return 1
    count = 1
    if args:
        if not args[0].isdigit() or int(args[0]) == 0:
            print(f"{name}: {args[0]}: loop count out of range", file=stderr)
            return 1
        count = int(args[0])
    if state.loop_depth == 0:
        print(f"{name}: only meaningful in a `for', `while', or `until' loop", file=stderr)
        return 0
    raise exc(min(count, state.loop_depth))


def builtin_break(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    return loop_control("break", BreakLoop, state, args, stderr)


def builtin_continue(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    return loop_control("continue", ContinueLoop, state, args, stderr)


def builtin_guide(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    from .guide import guide_main  # imported here: guide needs BUILTINS from this module
    return guide_main(state, args, stdout, stderr)


class LineSource:
    """Hands out the lines of a script one at a time. A command that needs more lines
    (a here-document) takes them from the same source."""

    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.number = 0  # lines handed out so far

    def next(self) -> Optional[str]:
        if self.number >= len(self.lines):
            return None
        line = self.lines[self.number]
        self.number += 1
        return line


def run_script(state: ShellState, text: str, label: str, stderr: TextIO) -> int:
    """Run each line of text in the current shell. Returns the last status."""
    status = 0
    source = LineSource(text)
    while True:
        line = source.next()
        if line is None:
            return status
        number = source.number  # the line where this command starts
        if not line.strip():
            continue
        try:
            status = state.execute(line, source.next)
        except ShellError as e:
            print(f"{label}: line {number}: {e}", file=stderr)
            status = state.last_status = 2


def builtin_history(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    """history [N] | -c | -d N | -w [file] | -r [file]"""
    total = history.length()
    if not args or args[0].isdigit():
        if len(args) > 1:
            print("history: too many arguments", file=stderr)
            return 1
        start = max(0, total - int(args[0])) if args else 0
        for k in range(start + 1, total + 1):
            print(f"{k:5d}  {history.item(k)}", file=stdout)
        return 0
    flag = args[0]
    if flag == "-c":
        history.clear()
        return 0
    if flag == "-d":
        if len(args) < 2:
            print("history: -d: option requires an argument", file=stderr)
            return 2
        if not args[1].isdigit():
            print(f"history: {args[1]}: numeric argument required", file=stderr)
            return 1
        position = int(args[1])
        if not 1 <= position <= total:
            print(f"history: {position}: history position out of range", file=stderr)
            return 1
        history.remove(position)
        return 0
    if flag in ("-w", "-r"):
        path = args[1] if len(args) > 1 else state.history_file
        try:
            if flag == "-w":
                history.write(path)
            else:
                history.read(path)
        except OSError as e:
            print(f"history: {path}: {e.strerror}", file=stderr)
            return 1
        return 0
    if flag.startswith("-") and len(flag) > 1:
        print(f"history: {flag}: invalid option", file=stderr)
        return 2
    print(f"history: {flag}: numeric argument required", file=stderr)
    return 1


def source_file(state: ShellState, path: str, stderr: TextIO,
                args: Optional[List[str]] = None) -> int:
    """Run each line of a file in the current shell. Extra args become $1, $2, ... meanwhile."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        print(f"source: {path}: {e.strerror}", file=stderr)
        return 1
    saved = state.positional
    if args:
        state.positional = list(args)
    state.func_depth += 1  # return works inside a sourced file
    try:
        return run_script(state, text, path, stderr)
    except ReturnFromFunction as r:
        return r.status
    finally:
        state.func_depth -= 1
        state.positional = saved


def builtin_source(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not args:
        print("source: filename argument required", file=stderr)
        return 2
    return source_file(state, args[0], stderr, args[1:])


BUILTINS: Dict[str, Builtin] = {
    "echo": builtin_echo,
    "pwd": builtin_pwd,
    "cd": builtin_cd,
    "alias": builtin_alias,
    "exit": builtin_exit,
    "type": builtin_type,
    "export": builtin_export,
    "unset": builtin_unset,
    "source": builtin_source,
    ".": builtin_source,
    "jobs": builtin_jobs,
    "fg": builtin_fg,
    "bg": builtin_bg,
    "wait": builtin_wait,
    "history": builtin_history,
    "test": builtin_test,
    "[": builtin_bracket,
    "guide": builtin_guide,
    "break": builtin_break,
    "continue": builtin_continue,
}
BUILTINS.update(EXTRA_BUILTINS)
