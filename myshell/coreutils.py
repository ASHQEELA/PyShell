"""Portable in-process versions of common Unix tools, so the shell works on every OS.

Each command takes (state, args, stdout, stderr) and returns an exit status:
0 ok, 1 failure (or no match for grep), 2 usage error. Input comes from
state.input_stream(), never from sys.stdin directly."""
import bisect
import contextlib
import math
import os
import re
import shutil
import stat
import string
import time
from typing import Callable, Dict, Iterator, List, Optional, TextIO, Tuple

from .state import ShellState

Command = Callable[[ShellState, List[str], TextIO, TextIO], int]

Parsed = Tuple[set, Dict[str, List[str]], List[str]]


# ---------------------------------------------------------------- helpers

def _parse(name: str, args: List[str], allowed: str, stderr: TextIO,
           valued: str = "", numeric: bool = False) -> Optional[Parsed]:
    """Split args into (flags, option values, operands). Returns None after
    printing an error. Handles -abc clusters, -- and options after operands."""
    flags = set()  # type: set
    values = {}  # type: Dict[str, List[str]]
    rest = []  # type: List[str]
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            rest.extend(args[i:])
            break
        if numeric and re.fullmatch(r"-\d+", arg):
            values.setdefault("n", []).append(arg[1:])  # head -5
            continue
        if arg.startswith("--"):
            print(f"{name}: unrecognized option '{arg}'", file=stderr)
            return None
        if len(arg) > 1 and arg[0] == "-":
            j = 1
            while j < len(arg):
                ch = arg[j]
                j += 1
                if ch in valued:
                    if j < len(arg):
                        val = arg[j:]  # -n5
                    elif i < len(args):
                        val = args[i]  # -n 5
                        i += 1
                    else:
                        print(f"{name}: option requires an argument -- '{ch}'", file=stderr)
                        return None
                    values.setdefault(ch, []).append(val)
                    break
                if ch not in allowed:
                    print(f"{name}: invalid option -- '{ch}'", file=stderr)
                    return None
                flags.add(ch)
        else:
            rest.append(arg)
    return flags, values, rest


def _oserr(name: str, path: str, exc: OSError, stderr: TextIO) -> None:
    print(f"{name}: {path}: {exc.strerror or exc}", file=stderr)


def _open_text(path: str) -> TextIO:
    return open(path, encoding="utf-8", errors="replace", newline="")


@contextlib.contextmanager
def _open_input(state: ShellState, path: str) -> Iterator[TextIO]:
    """Yield a text stream for a file name, or the input stream for '-'."""
    if path == "-":
        yield state.input_stream()
    else:
        with _open_text(path) as f:
            yield f


# ---------------------------------------------------------------- ls

def _human_size(size: int) -> str:
    if size < 1024:
        return str(size)
    value = float(size)
    for unit in "KMGTP":
        value /= 1024
        if value < 1024 or unit == "P":
            break
    value = math.ceil(value * 10) / 10
    return f"{value:.1f}{unit}" if value < 10 else f"{math.ceil(value)}{unit}"


def _name_key(name: str) -> Tuple[str, str]:
    return (name.lower(), name)


def _pack_columns(names: List[str], width: int) -> List[str]:
    """Lay names out in columns, filled top to bottom like ls."""
    best = [[n] for n in names]  # fallback: one column
    for ncols in range(len(names), 0, -1):
        rows = math.ceil(len(names) / ncols)
        cols = [names[i * rows:(i + 1) * rows] for i in range(ncols)]
        cols = [c for c in cols if c]
        widths = [max(len(n) for n in c) + 2 for c in cols]
        if sum(widths) - 2 <= width:
            best = cols
            break
    else:
        widths = [0]
    rows = len(best[0])
    widths = [max(len(n) for n in c) + 2 for c in best]
    lines = []
    for r in range(rows):
        cells = [c[r].ljust(w) for c, w in zip(best, widths) if r < len(c)]
        lines.append("".join(cells).rstrip())
    return lines


def _ls_emit(entries: List[Tuple[str, str]], flags: set, stdout: TextIO, stderr: TextIO) -> int:
    """Print (display name, real path) entries. Returns a status."""
    status = 0
    if "l" in flags:
        rows = []
        for display, path in entries:
            try:
                st = os.lstat(path)
            except OSError as e:
                print(f"ls: cannot access '{display}': {e.strerror or e}", file=stderr)
                status = 2
                continue
            size = _human_size(st.st_size) if "h" in flags else str(st.st_size)
            date = time.strftime("%b %d %H:%M", time.localtime(st.st_mtime))
            label = display
            if stat.S_ISLNK(st.st_mode):
                try:
                    label += " -> " + os.readlink(path)
                except OSError:
                    pass
            rows.append((stat.filemode(st.st_mode), size, date, label))
        width = max((len(r[1]) for r in rows), default=0)
        for mode, size, date, label in rows:
            print(f"{mode} {size.rjust(width)} {date} {label}", file=stdout)
        return status
    names = [d for d, _ in entries]
    is_tty = getattr(stdout, "isatty", lambda: False)()
    if is_tty and "1" not in flags and names:
        for line in _pack_columns(names, shutil.get_terminal_size().columns):
            print(line, file=stdout)
    else:
        for n in names:
            print(n, file=stdout)
    return status


def ls(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("ls", args, "al1hd", stderr)
    if parsed is None:
        return 2
    flags, _, paths = parsed
    paths = paths or ["."]
    status = 0
    files = []  # type: List[Tuple[str, str]]
    dirs = []  # type: List[str]
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            try:
                st = os.lstat(p)  # a broken link still exists
            except OSError as e:
                print(f"ls: cannot access '{p}': {e.strerror or e}", file=stderr)
                status = 2
                continue
        if stat.S_ISDIR(st.st_mode) and "d" not in flags:
            dirs.append(p)
        else:
            files.append((p, p))
    files.sort(key=lambda e: _name_key(e[0]))
    dirs.sort(key=_name_key)
    printed = False
    if files:
        if _ls_emit(files, flags, stdout, stderr):
            status = 2
        printed = True
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError as e:
            print(f"ls: cannot open directory '{d}': {e.strerror or e}", file=stderr)
            status = 2
            continue
        if "a" in flags:
            names += [".", ".."]
        else:
            names = [n for n in names if not n.startswith(".")]
        names.sort(key=_name_key)
        if printed:
            print(file=stdout)
        if len(paths) > 1:
            print(f"{d}:", file=stdout)
        printed = True
        if _ls_emit([(n, os.path.join(d, n)) for n in names], flags, stdout, stderr):
            status = 2
    return status


# ---------------------------------------------------------------- cat

def cat(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("cat", args, "n", stderr)
    if parsed is None:
        return 2
    flags, _, files = parsed
    status = 0
    number = 0
    for f in files or ["-"]:
        try:
            with _open_input(state, f) as src:
                for line in src:
                    if "n" in flags:
                        number += 1
                        stdout.write(f"{number:6}\t")
                    stdout.write(line)
        except OSError as e:
            _oserr("cat", f, e, stderr)
            status = 1
    return status


# ---------------------------------------------------------------- head

def head(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("head", args, "", stderr, valued="n", numeric=True)
    if parsed is None:
        return 2
    _, values, files = parsed
    count = 10
    if "n" in values:
        text = values["n"][-1]
        try:
            count = int(text)
        except ValueError:
            count = -1
        if count < 0:
            print(f"head: invalid number of lines: '{text}'", file=stderr)
            return 1
    files = files or ["-"]
    status = 0
    first = True
    for f in files:
        try:
            with _open_input(state, f) as src:
                if len(files) > 1:
                    if not first:
                        stdout.write("\n")
                    stdout.write(f"==> {'standard input' if f == '-' else f} <==\n")
                first = False
                for _ in range(count):
                    line = src.readline()  # stop early, do not read the rest
                    if not line:
                        break
                    stdout.write(line)
        except OSError as e:
            _oserr("head", f, e, stderr)
            status = 1
    return status


# ---------------------------------------------------------------- tr

_CLASSES = {
    "lower": string.ascii_lowercase,
    "upper": string.ascii_uppercase,
    "digit": string.digits,
    "alpha": string.ascii_letters,
    "alnum": string.ascii_letters + string.digits,
    "space": "\t\n\v\f\r ",
    "punct": string.punctuation,
}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def _tr_atom(s: str, i: int) -> Tuple[str, bool, int]:
    """Read one item at s[i]: a char, an escape or a [:class:]. Returns (chars, is_class, next)."""
    c = s[i]
    if c == "\\" and i + 1 < len(s):
        d = s[i + 1]
        if d in "01234567":
            j = i + 1
            while j < len(s) and j < i + 4 and s[j] in "01234567":
                j += 1
            return chr(int(s[i + 1:j], 8)), False, j
        return _ESCAPES.get(d, d), False, i + 2
    if s.startswith("[:", i):
        j = s.find(":]", i + 2)
        if j > 0:
            name = s[i + 2:j]
            if name not in _CLASSES:
                raise ValueError(f"invalid character class '{s[i:j + 2]}'")
            return _CLASSES[name], True, j + 2
    return c, False, i + 1


def _tr_expand(s: str) -> List[str]:
    out = []  # type: List[str]
    i = 0
    while i < len(s):
        chars, is_class, i = _tr_atom(s, i)
        if not is_class and i + 1 < len(s) and s[i] == "-":
            end, end_is_class, j = _tr_atom(s, i + 1)
            if not end_is_class:
                if ord(end) < ord(chars):
                    raise ValueError(f"range-endpoints of '{chars}-{end}' are in reverse "
                                     "collating sequence order")
                out.extend(chr(x) for x in range(ord(chars), ord(end) + 1))
                i = j
                continue
        out.extend(chars)
    return out


def tr(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("tr", args, "dsc", stderr)
    if parsed is None:
        return 2
    flags, _, sets = parsed
    delete, squeeze, comp = "d" in flags, "s" in flags, "c" in flags
    if not sets:
        print("tr: missing operand", file=stderr)
        return 2
    if len(sets) > 2 or (delete and not squeeze and len(sets) > 1):
        print(f"tr: extra operand '{sets[2] if len(sets) > 2 else sets[1]}'", file=stderr)
        return 2
    translate = not delete and len(sets) == 2
    if not delete and not squeeze and len(sets) == 1:
        print(f"tr: missing operand after '{sets[0]}'", file=stderr)
        print("Two strings must be given when translating.", file=stderr)
        return 2
    try:
        set1 = _tr_expand(sets[0])
        set2 = _tr_expand(sets[1]) if len(sets) == 2 else []
    except ValueError as e:
        print(f"tr: {e}", file=stderr)
        return 2
    if translate and not set2 and set1:
        print("tr: when not truncating set1, string2 must be non-empty", file=stderr)
        return 2

    members = set(set1)
    sorted_ords = sorted(ord(ch) for ch in members)

    def in_set1(ch: str) -> bool:
        return (ch in members) != comp

    def translate_char(ch: str) -> str:
        if not in_set1(ch):
            return ch
        if comp:
            rank = ord(ch) - bisect.bisect_left(sorted_ords, ord(ch))  # place among the complement
            return set2[min(rank, len(set2) - 1)]
        return mapping.get(ch, ch)

    mapping = {}  # type: Dict[str, str]
    if translate and not comp:
        for k, ch in enumerate(set1):
            mapping[ch] = set2[min(k, len(set2) - 1)]
    squeeze_set = set(set2) if (squeeze and set2) else None

    out = []  # type: List[str]
    last = None  # type: Optional[str]
    for ch in state.input_stream().read():
        if delete and in_set1(ch):
            continue
        if translate:
            ch = translate_char(ch)
        if squeeze and ch == last:
            if (ch in squeeze_set) if squeeze_set is not None else in_set1(ch):
                continue
        out.append(ch)
        last = ch
    stdout.write("".join(out))
    return 0


# ---------------------------------------------------------------- sleep

def sleep(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        print("sleep: missing operand", file=stderr)
        return 1
    total = 0.0
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    for a in args:
        scale = 1
        text = a
        if a and a[-1] in units:
            scale = units[a[-1]]
            text = a[:-1]
        try:
            value = float(text)
        except ValueError:
            value = -1.0
        if not (0 <= value < float("inf")):
            print(f"sleep: invalid time interval '{a}'", file=stderr)
            return 1
        total += value * scale
    time.sleep(total)  # KeyboardInterrupt propagates
    return 0


# ---------------------------------------------------------------- mkdir

def mkdir(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("mkdir", args, "p", stderr)
    if parsed is None:
        return 2
    flags, _, dirs = parsed
    if not dirs:
        print("mkdir: missing operand", file=stderr)
        return 2
    status = 0
    for d in dirs:
        try:
            if "p" in flags:
                os.makedirs(d, exist_ok=True)
            else:
                os.mkdir(d)
        except OSError as e:
            print(f"mkdir: cannot create directory '{d}': {e.strerror or e}", file=stderr)
            status = 1
    return status


# ---------------------------------------------------------------- rm

def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        if os.name != "nt":
            raise
        if os.path.isdir(path):
            os.rmdir(path)  # a directory link on Windows
        else:
            os.chmod(path, stat.S_IWRITE)  # clear the read-only flag and retry once
            os.remove(path)


def _remove_tree(path: str) -> None:
    try:
        shutil.rmtree(path)
    except PermissionError:
        if os.name != "nt":
            raise
        for root, dirnames, filenames in os.walk(path):
            for n in dirnames + filenames:
                try:
                    os.chmod(os.path.join(root, n), stat.S_IWRITE)
                except OSError:
                    pass
        shutil.rmtree(path)


def _is_dangerous(path: str) -> bool:
    """True for a filesystem root or the home folder."""
    if os.path.islink(path):
        return False  # removing a link does not touch its target
    real = os.path.normcase(os.path.realpath(path))
    if os.path.dirname(real) == real:
        return True
    homes = [os.environ.get("HOME"), os.path.expanduser("~")]
    return any(h and os.path.normcase(os.path.realpath(h)) == real for h in homes)


def rm(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("rm", args, "rRf", stderr)
    if parsed is None:
        return 2
    flags, _, paths = parsed
    force = "f" in flags
    recursive = "r" in flags or "R" in flags
    if not paths:
        if force:
            return 0
        print("rm: missing operand", file=stderr)
        return 1
    status = 0
    for p in paths:
        if os.path.basename(p.rstrip("/\\")) in (".", ".."):
            print(f"rm: refusing to remove '.' or '..' directory: skipping '{p}'", file=stderr)
            status = 1
            continue
        if not os.path.lexists(p):
            if not force:
                print(f"rm: cannot remove '{p}': No such file or directory", file=stderr)
                status = 1
            continue
        if _is_dangerous(p):
            print(f"rm: it is dangerous to operate recursively on '{p}'", file=stderr)
            status = 1
            continue
        is_dir = os.path.isdir(p) and not os.path.islink(p)
        if is_dir and not recursive:
            print(f"rm: cannot remove '{p}': Is a directory", file=stderr)
            status = 1
            continue
        try:
            if is_dir:
                _remove_tree(p)
            else:
                _remove_file(p)
        except OSError as e:
            print(f"rm: cannot remove '{p}': {e.strerror or e}", file=stderr)
            status = 1
    return status


# ---------------------------------------------------------------- cp and mv

def _pairs(name: str, operands: List[str], stderr: TextIO) -> Tuple[List[Tuple[str, str]], int]:
    """Turn SRC... DEST into (source, target) pairs. Status is nonzero on a usage error."""
    if not operands:
        print(f"{name}: missing file operand", file=stderr)
        return [], 2
    if len(operands) == 1:
        print(f"{name}: missing destination file operand after '{operands[0]}'", file=stderr)
        return [], 2
    dest = operands[-1]
    srcs = operands[:-1]
    if os.path.isdir(dest):
        pairs = []
        for s in srcs:
            base = os.path.basename(os.path.abspath(s)) or s
            pairs.append((s, os.path.join(dest, base)))
        return pairs, 0
    if len(srcs) > 1:
        print(f"{name}: target '{dest}' is not a directory", file=stderr)
        return [], 1
    return [(srcs[0], dest)], 0


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def cp(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("cp", args, "rRfn", stderr)
    if parsed is None:
        return 2
    flags, _, operands = parsed
    pairs, status = _pairs("cp", operands, stderr)
    if status:
        return status
    recursive = "r" in flags or "R" in flags
    for src, dst in pairs:
        if not os.path.lexists(src):
            print(f"cp: cannot stat '{src}': No such file or directory", file=stderr)
            status = 1
            continue
        if _same_file(src, dst):
            print(f"cp: '{src}' and '{dst}' are the same file", file=stderr)
            status = 1
            continue
        src_is_dir = os.path.isdir(src)
        if src_is_dir and not recursive:
            print(f"cp: -r not specified; omitting directory '{src}'", file=stderr)
            status = 1
            continue
        if "n" in flags and os.path.lexists(dst):
            continue
        try:
            if src_is_dir:
                if os.path.lexists(dst) and not os.path.isdir(dst):
                    print(f"cp: cannot overwrite non-directory '{dst}' with directory '{src}'",
                          file=stderr)
                    status = 1
                    continue
                inside = os.path.realpath(dst) + os.sep
                if inside.startswith(os.path.realpath(src) + os.sep):
                    print(f"cp: cannot copy a directory, '{src}', into itself, '{dst}'",
                          file=stderr)
                    status = 1
                    continue
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif os.path.isdir(dst):
                print(f"cp: cannot overwrite directory '{dst}' with non-directory", file=stderr)
                status = 1
            else:
                try:
                    shutil.copy2(src, dst)
                except PermissionError:
                    if "f" not in flags or not os.path.lexists(dst):
                        raise
                    os.remove(dst)  # -f: replace a file we cannot write
                    shutil.copy2(src, dst)
        except (OSError, shutil.Error) as e:
            print(f"cp: cannot copy '{src}' to '{dst}': {getattr(e, 'strerror', None) or e}",
                  file=stderr)
            status = 1
    return status


def mv(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("mv", args, "fn", stderr)
    if parsed is None:
        return 2
    flags, _, operands = parsed
    pairs, status = _pairs("mv", operands, stderr)
    if status:
        return status
    for src, dst in pairs:
        if not os.path.lexists(src):
            print(f"mv: cannot stat '{src}': No such file or directory", file=stderr)
            status = 1
            continue
        if _same_file(src, dst):
            print(f"mv: '{src}' and '{dst}' are the same file", file=stderr)
            status = 1
            continue
        if "n" in flags and os.path.lexists(dst):
            continue
        src_is_dir = os.path.isdir(src) and not os.path.islink(src)
        try:
            if os.path.isdir(dst) and not os.path.islink(dst):
                if not src_is_dir:
                    print(f"mv: cannot overwrite directory '{dst}' with non-directory",
                          file=stderr)
                    status = 1
                    continue
                os.rmdir(dst)  # only an empty folder can be replaced
            elif os.path.lexists(dst):
                if src_is_dir:
                    print(f"mv: cannot overwrite non-directory '{dst}' with directory '{src}'",
                          file=stderr)
                    status = 1
                    continue
                if os.name == "nt":
                    _remove_file(dst)  # rename does not replace on Windows
            shutil.move(src, dst)
        except (OSError, shutil.Error) as e:
            print(f"mv: cannot move '{src}' to '{dst}': {getattr(e, 'strerror', None) or e}",
                  file=stderr)
            status = 1
    return status


# ---------------------------------------------------------------- touch

def touch(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("touch", args, "c", stderr)
    if parsed is None:
        return 2
    flags, _, files = parsed
    if not files:
        print("touch: missing file operand", file=stderr)
        return 2
    status = 0
    for f in files:
        try:
            if os.path.exists(f):
                os.utime(f, None)
            elif "c" not in flags:
                open(f, "a").close()
        except OSError as e:
            print(f"touch: cannot touch '{f}': {e.strerror or e}", file=stderr)
            status = 1
    return status


# ---------------------------------------------------------------- wc

def wc(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("wc", args, "lwcm", stderr)
    if parsed is None:
        return 2
    flags, _, files = parsed
    if not flags:
        flags = {"l", "w", "c"}
    status = 0
    rows = []  # type: List[Tuple[List[int], str]]
    for f in files or ["-"]:
        try:
            if f == "-":
                text = state.input_stream().read()
                size = len(text.encode("utf-8", errors="replace"))
            else:
                with open(f, "rb") as fh:
                    raw = fh.read()
                size = len(raw)
                text = raw.decode("utf-8", errors="replace")
        except OSError as e:
            _oserr("wc", f, e, stderr)
            status = 1
            continue
        # order matches GNU: lines, words, characters, bytes
        counts = {"l": text.count("\n"), "w": len(text.split()), "m": len(text), "c": size}
        rows.append(([counts[k] for k in "lwmc" if k in flags], "" if not files else f))
    if len(files) > 1:
        totals = [sum(r[0][k] for r in rows) for k in range(len([k for k in "lwmc" if k in flags]))]
        rows.append((totals, "total"))
    width = max((len(str(n)) for r in rows for n in r[0]), default=1)
    for counts_list, label in rows:
        line = " ".join(str(n).rjust(width) for n in counts_list)
        print(f"{line} {label}" if label else line, file=stdout)
    return status


# ---------------------------------------------------------------- grep

def _grep_stream(src: TextIO, label: str, rx: "re.Pattern", flags: set, show_name: bool,
                 stdout: TextIO) -> int:
    """Search one stream. Prints matching lines unless -c/-l/-q. Returns the match count."""
    quiet = bool({"c", "l", "q"} & flags)
    count = 0
    for lineno, line in enumerate(src, 1):
        content = line.rstrip("\r\n")
        if bool(rx.search(content)) == ("v" in flags):
            continue
        count += 1
        if "l" in flags or "q" in flags:
            break  # one match is enough
        if not quiet:
            prefix = (label + ":" if show_name else "") + (f"{lineno}:" if "n" in flags else "")
            stdout.write(prefix + line + ("" if line.endswith("\n") else "\n"))
    return count


def _grep_targets(path: str, recursive: bool, stderr: TextIO) -> Iterator[Tuple[str, bool]]:
    """Yield (file name, from_walk). A folder is walked only with -r."""
    if os.path.isdir(path):
        if not recursive:
            print(f"grep: {path}: Is a directory", file=stderr)
            yield path, False  # reported below as an error
            return
        for root, dirnames, filenames in os.walk(path):
            dirnames.sort()
            for n in sorted(filenames):
                p = os.path.join(root, n)
                yield (os.path.normpath(p) if path == "." else p), True
    else:
        yield path, False


def grep(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    parsed = _parse("grep", args, "ivnclqwFErHhR", stderr, valued="e")
    if parsed is None:
        return 2
    flags, values, operands = parsed
    patterns = []  # type: List[str]
    for p in values.get("e", []):
        patterns.extend(p.split("\n"))
    if "e" not in values:
        if not operands:
            print("Usage: grep [OPTION]... PATTERNS [FILE]...", file=stderr)
            return 2
        patterns = operands[0].split("\n")
        operands = operands[1:]
    parts = [re.escape(p) if "F" in flags else p for p in patterns]
    body = "|".join(f"(?:{p})" for p in parts)
    if "w" in flags:
        body = rf"(?<!\w)(?:{body})(?!\w)"
    try:
        rx = re.compile(body, re.IGNORECASE if "i" in flags else 0)
    except re.error:
        print("grep: invalid regular expression", file=stderr)
        return 2

    recursive = "r" in flags or "R" in flags
    files = operands
    if not files:
        files = ["."] if recursive else ["-"]
    # prefix lines with the file name when several files can be searched
    if "H" in flags:
        show_name = True
    elif "h" in flags:
        show_name = False
    else:
        show_name = len(files) > 1 or (recursive and any(os.path.isdir(f) for f in files))

    matched = False
    error = False
    for arg in files:
        for path, walked in _grep_targets(arg, recursive, stderr):
            if os.path.isdir(path) and not walked:
                error = True
                continue
            label = "(standard input)" if path == "-" else path
            try:
                with _open_input(state, path) as src:
                    count = _grep_stream(src, label, rx, flags, show_name, stdout)
            except OSError as e:
                if not walked:  # skip unreadable files found by -r
                    _oserr("grep", path, e, stderr)
                    error = True
                continue
            if count:
                matched = True
                if "q" in flags:
                    return 0
                if "l" in flags:
                    print(label, file=stdout)
            if "c" in flags and "l" not in flags:
                print((label + ":" if show_name else "") + str(count), file=stdout)
    if error:
        return 2
    return 0 if matched else 1


PORTABLE = {
    "ls": ls,
    "cat": cat,
    "head": head,
    "tr": tr,
    "sleep": sleep,
    "mkdir": mkdir,
    "rm": rm,
    "cp": cp,
    "mv": mv,
    "touch": touch,
    "wc": wc,
    "grep": grep,
}  # type: Dict[str, Command]
