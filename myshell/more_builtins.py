"""More builtins: true, false, :, return, shift, local, read, printf, set, env, which,
unalias, exec and trap."""
import os
import re
import shutil
import signal
from typing import Callable, Dict, List, Optional, TextIO

from .errors import ReturnFromFunction, ShellExit
from .state import ShellState

Builtin = Callable[[ShellState, List[str], TextIO, TextIO], int]
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

ECHO_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
                "\\": "\\", "e": "\x1b"}


def unescape(text: str) -> str:
    """Backslash escapes as in echo -e and printf: \\n \\t \\\\ \\0NNN \\xHH."""
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c != "\\" or i + 1 >= len(text):
            out.append(c)
            i += 1
            continue
        d = text[i + 1]
        if d in ECHO_ESCAPES:
            out.append(ECHO_ESCAPES[d])
            i += 2
        elif d == "0":
            m = re.compile(r"[0-7]{1,3}").match(text, i + 2)
            out.append(chr(int(m.group(), 8)) if m else "\0")
            i = m.end() if m else i + 2
        elif d == "x":
            m = re.compile(r"[0-9a-fA-F]{1,2}").match(text, i + 2)
            out.append(chr(int(m.group(), 16)) if m else "\\x")
            i = m.end() if m else i + 2
        else:
            out.append(c + d)
            i += 2
    return "".join(out)




def builtin_true(state, args, stdout, stderr) -> int:
    return 0


def builtin_false(state, args, stdout, stderr) -> int:
    return 1


# --- functions and arguments ---
def builtin_return(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if len(args) > 1:
        print("return: too many arguments", file=stderr)
        return 1
    if state.func_depth == 0:
        print("return: can only `return' from a function or sourced script", file=stderr)
        return 1
    code = state.last_status
    if args:
        try:
            code = int(args[0]) & 0xFF
        except ValueError:
            print(f"return: {args[0]}: numeric argument required", file=stderr)
            code = 2
    raise ReturnFromFunction(code)


def builtin_shift(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    count = 1
    if args:
        if not args[0].isdigit():
            print(f"shift: {args[0]}: numeric argument required", file=stderr)
            return 1
        count = int(args[0])
    if count > len(state.positional):
        print("shift: shift count out of range", file=stderr)
        return 1
    del state.positional[:count]
    return 0


def builtin_local(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not state.local_frames:
        print("local: can only be used in a function", file=stderr)
        return 1
    frame = state.local_frames[-1]
    status = 0
    for arg in args:
        name, sep, value = arg.partition("=")
        if not NAME_RE.fullmatch(name):
            print(f"local: `{arg}': not a valid identifier", file=stderr)
            status = 1
            continue
        if name not in frame:
            frame[name] = state.get_variable(name)
        if sep:
            state.set_variable(name, value)
        elif name not in os.environ:
            state.variables[name] = ""
    return status


# --- read ---
def builtin_read(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    """read [-r] [-p PROMPT] [-d DELIM] [-n COUNT] [NAME...]. Without a NAME the line goes to REPLY."""
    raw, prompt, delim, count = False, None, "\n", None
    names: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-r":
            raw = True
        elif a in ("-p", "-d", "-n"):
            if i + 1 >= len(args):
                print(f"read: {a}: option requires an argument", file=stderr)
                return 2
            i += 1
            if a == "-p":
                prompt = args[i]
            elif a == "-d":
                delim = args[i][:1] or "\0"
            else:
                if not args[i].isdigit():
                    print(f"read: {args[i]}: invalid number", file=stderr)
                    return 2
                count = int(args[i])
        elif a.startswith("-") and len(a) > 1:
            print(f"read: {a}: invalid option", file=stderr)
            return 2
        else:
            names.append(a)
        i += 1
    bad = [n for n in names if not NAME_RE.fullmatch(n)]
    if bad:
        print(f"read: `{bad[0]}': not a valid identifier", file=stderr)
        return 1
    stream = state.input_stream()
    if prompt is not None and getattr(stream, "isatty", lambda: False)():
        stderr.write(prompt)
        stderr.flush()
    line, got_delim = "", False
    while True:
        if delim == "\n" and count is None:
            chunk = stream.readline()
            if not chunk:
                break
            got_delim = chunk.endswith("\n")
            line += chunk[:-1] if got_delim else chunk
            if not raw and got_delim and re.search(r"(?<!\\)(\\\\)*\\$", line):
                line = line[:-1]  # a trailing backslash joins the next line
                continue
            break
        ch = stream.read(1)
        if not ch:
            break
        if ch == delim:
            got_delim = True
            break
        line += ch
        if count is not None and len(line) >= count:
            got_delim = True
            break
    if not raw:
        line = re.sub(r"\\(.)", r"\1", line, flags=re.DOTALL)
    names = names or ["REPLY"]
    ifs = state.get_variable("IFS")
    ifs = " \t\n" if ifs is None else ifs
    if ifs.strip(" \t\n") == "" and ifs:
        fields = line.split(None, len(names) - 1) if len(names) > 1 else [line.strip()]
    elif ifs:
        pattern = "[" + re.escape(ifs) + "]"
        fields = re.split(pattern, line, maxsplit=len(names) - 1)
    else:
        fields = [line]
    for k, name in enumerate(names):
        state.set_variable(name, fields[k] if k < len(fields) else "")
    return 0 if got_delim else 1


# --- printf ---
SPEC_RE = re.compile(r"%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d+))?([diouxXeEfFgGcsb%q])")


def to_int(text: str) -> int:
    if text[:1] in ("'", '"') and len(text) > 1:
        return ord(text[1])
    if re.fullmatch(r"[+-]?0[0-7]+", text):
        return int(text, 8)  # a leading zero means octal, as in other shells
    try:
        return int(text, 0)
    except ValueError:
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text, 10)
        raise ValueError(text)


def format_once(fmt: str, args: List[str], pos: int, stderr: TextIO, errors: List[bool]):
    """Format fmt once. Returns (text, next argument position, arguments used)."""
    out: List[str] = []
    used = 0
    i = 0
    while i < len(fmt):
        c = fmt[i]
        if c == "\\":
            m = re.compile(r"\\(0[0-7]{0,3}|x[0-9a-fA-F]{1,2}|[1-7][0-7]{0,2}|.)", re.DOTALL).match(fmt, i)
            if m:
                code = m.group(1)
                out.append(chr(int(code, 8)) if code[0] in "1234567" and len(code) > 0
                           and code.isdigit() else unescape(m.group()))
                i = m.end()
            else:
                out.append(c)
                i += 1
            continue
        if c != "%":
            out.append(c)
            i += 1
            continue
        m = SPEC_RE.match(fmt, i)
        if not m:
            out.append(c)
            i += 1
            continue
        i = m.end()
        flags, width, prec, conv = m.groups()
        if conv == "%":
            out.append("%")
            continue

        def next_arg() -> Optional[str]:
            nonlocal pos, used
            if pos < len(args):
                pos += 1
                used += 1
                return args[pos - 1]
            return None
        if width == "*":
            width = next_arg() or "0"
        if prec == "*":
            prec = next_arg() or "0"
        arg = next_arg()
        spec = "%" + flags + (width or "") + ("." + prec if prec is not None else "")
        try:
            if conv in "di":
                out.append((spec + "d") % to_int(arg or "0"))
            elif conv in "ouxX":
                out.append((spec + conv) % to_int(arg or "0"))
            elif conv in "eEfFgG":
                out.append((spec + conv) % float(arg or "0"))
            elif conv == "c":
                out.append((spec + "s") % (arg or "")[:1])
            elif conv == "b":
                out.append((spec + "s") % unescape(arg or ""))
            elif conv == "q":
                out.append((spec + "s") % ("'" + (arg or "").replace("'", "'\\''") + "'"))
            else:
                out.append((spec + "s") % (arg or ""))
        except (ValueError, TypeError):
            print(f"printf: {arg}: invalid number", file=stderr)
            errors.append(True)
            out.append((spec + "s") % "0" if conv not in "csbq" else "")
    return "".join(out), pos, used


def builtin_printf(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    var = None
    if len(args) >= 2 and args[0] == "-v":
        var, args = args[1], args[2:]
    if not args:
        print("printf: usage: printf [-v var] format [arguments]", file=stderr)
        return 2
    fmt, rest = args[0], args[1:]
    errors: List[bool] = []
    pieces: List[str] = []
    pos = 0
    while True:
        text, pos, used = format_once(fmt, rest, pos, stderr, errors)
        pieces.append(text)
        if pos >= len(rest) or used == 0:
            break
    result = "".join(pieces)
    if var:
        state.set_variable(var, result)
    else:
        stdout.write(result)
    return 1 if errors else 0


# --- set ---
OPTION_NAMES = {"e": "errexit", "u": "nounset", "x": "xtrace"}


def builtin_set(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not args:
        names = {**{k: v for k, v in os.environ.items()}, **state.variables}
        for k in sorted(names):
            print(f"{k}={names[k]}", file=stdout)
        return 0
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            state.positional = list(args[i + 1:])
            return 0
        if a in ("-o", "+o"):
            if i + 1 >= len(args):
                for name in OPTION_NAMES.values():
                    print(f"{name:<10}{'on' if name in state.options else 'off'}", file=stdout)
                return 0
            name = args[i + 1]
            if name not in OPTION_NAMES.values():
                print(f"set: {name}: invalid option name", file=stderr)
                return 2
            (state.options.add if a == "-o" else state.options.discard)(name)
            i += 2
            continue
        if re.fullmatch(r"[-+][a-zA-Z]+", a):
            for ch in a[1:]:
                if ch not in OPTION_NAMES:
                    print(f"set: {a[0]}{ch}: invalid option", file=stderr)
                    return 2
                (state.options.add if a[0] == "-" else state.options.discard)(OPTION_NAMES[ch])
            i += 1
            continue
        break
    if i < len(args):
        state.positional = list(args[i:])
    return 0


# --- env, which, unalias, exec ---
def builtin_env(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    """env [-i] [-u NAME] [NAME=VALUE]... [COMMAND [ARGS]]"""
    saved = dict(os.environ)
    clear: bool = False
    unset: List[str] = []
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "-":
        if args[i] == "-i":
            clear = True
        elif args[i] == "-u" and i + 1 < len(args):
            i += 1
            unset.append(args[i])
        else:
            print(f"env: invalid option -- '{args[i]}'", file=stderr)
            return 125
        i += 1
    assigns: Dict[str, str] = {}
    while i < len(args) and "=" in args[i] and not args[i].startswith("="):
        k, _, v = args[i].partition("=")
        assigns[k] = v
        i += 1
    command = args[i:]
    if not command:
        env = {} if clear else dict(saved)
        for k in unset:
            env.pop(k, None)
        env.update(assigns)
        for k, v in env.items():
            print(f"{k}={v}", file=stdout)
        return 0
    try:
        if clear:
            os.environ.clear()
        for k in unset:
            os.environ.pop(k, None)
        os.environ.update(assigns)
        stdout.flush()
        return state.executor.run_argv(command)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def builtin_which(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    from .builtins import find_builtin
    show_all = False
    if args and args[0] == "-a":
        show_all, args = True, args[1:]
    status = 0
    for name in args:
        found = False
        if name in state.aliases:
            print(f"{name}: aliased to {state.aliases[name]}", file=stdout)
            found = True
        if name in state.functions:
            print(f"{name}: shell function", file=stdout)
            found = True
        if find_builtin(name):
            print(f"{name}: shell built-in command", file=stdout)
            found = True
        if not found or show_all:
            paths = []
            exts = [""] + [e for e in os.environ.get("PATHEXT", "").split(os.pathsep) if e] \
                if os.name == "nt" else [""]
            for folder in os.environ.get("PATH", "").split(os.pathsep):
                for ext in exts:
                    full = os.path.join(folder or ".", name + ext)
                    if os.path.isfile(full) and os.access(full, os.X_OK) and full not in paths:
                        paths.append(full)
            for full in (paths if show_all else paths[:1]):
                print(full, file=stdout)
                found = True
        if not found:
            print(f"which: no {name} in ({os.environ.get('PATH', '')})", file=stderr)
            status = 1
    return status


def builtin_unalias(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not args:
        print("unalias: usage: unalias [-a] name [name ...]", file=stderr)
        return 2
    if args[0] == "-a":
        state.aliases.clear()
        return 0
    status = 0
    for name in args:
        if name in state.aliases:
            del state.aliases[name]
        else:
            print(f"unalias: {name}: not found", file=stderr)
            status = 1
    return status


def builtin_exec(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    """exec COMMAND [ARGS]: run it in place of the shell, then exit."""
    if not args:
        return 0
    stdout.flush()
    stderr.flush()
    from .builtins import find_builtin
    name = args[0]
    if name not in state.functions and not find_builtin(name):
        path = shutil.which(name)
        if path is None:
            print(f"exec: {name}: not found", file=stderr)
            return 127
        if hasattr(os, "execv") and os.name != "nt":
            history_save = getattr(state, "save_history", None)
            if history_save:
                history_save()
            try:
                os.execv(path, args)
            except OSError as e:
                print(f"exec: {name}: {e.strerror}", file=stderr)
                return 126
    status = state.executor.run_argv(args)
    raise ShellExit(status)


# --- trap ---
def signal_table() -> Dict[str, int]:
    table = {"EXIT": 0}
    for name in ("HUP", "INT", "QUIT", "USR1", "USR2", "TERM"):
        sig = getattr(signal, "SIG" + name, None)
        if sig is not None:
            table[name] = int(sig)
    return table


def signal_name(text: str) -> Optional[str]:
    table = signal_table()
    t = text.upper()
    if t.startswith("SIG"):
        t = t[3:]
    if t in table:
        return t
    if t.isdigit():
        for name, number in table.items():
            if number == int(t):
                return name
    return None


def builtin_trap(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    """trap 'COMMAND' SIGNAL...   trap - SIGNAL...   trap   trap -l"""
    table = signal_table()
    if not args or args == ["-p"]:
        for name, cmd in state.traps.items():
            print(f"trap -- '{cmd}' {name}", file=stdout)
        return 0
    if args[0] == "-l":
        print(" ".join(n for n in table if n != "EXIT"), file=stdout)
        return 0
    if len(args) == 1:
        action, names = "-", args  # trap SIGNAL resets it
    else:
        action, names = args[0], args[1:]
    status = 0
    for text in names:
        name = signal_name(text)
        if name is None:
            print(f"trap: {text}: invalid signal specification", file=stderr)
            status = 1
            continue
        if action == "-":
            state.traps.pop(name, None)
            if name != "EXIT":
                try:
                    signal.signal(table[name], signal.default_int_handler
                                  if name == "INT" else signal.SIG_DFL)
                except (ValueError, OSError):
                    pass
            continue
        state.traps[name] = action
        if name == "EXIT":
            continue
        try:
            if action == "":
                signal.signal(table[name], signal.SIG_IGN)
            else:
                signal.signal(table[name], lambda n, f, label=name: state.pending_traps.append(label))
        except (ValueError, OSError) as e:
            print(f"trap: {text}: {e}", file=stderr)
            state.traps.pop(name, None)
            status = 1
    return status


EXTRA_BUILTINS: Dict[str, Builtin] = {
    "true": builtin_true,
    "false": builtin_false,
    ":": builtin_true,
    "return": builtin_return,
    "shift": builtin_shift,
    "local": builtin_local,
    "read": builtin_read,
    "printf": builtin_printf,
    "set": builtin_set,
    "env": builtin_env,
    "which": builtin_which,
    "unalias": builtin_unalias,
    "exec": builtin_exec,
    "trap": builtin_trap,
}
