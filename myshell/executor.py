"""Executor: runs the parsed tree with fork and pipes, or with subprocess."""
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from . import compat
from .aliases import expand_aliases
from .builtins import BUILTINS
from .errors import ShellError, ShellExit
from .expand import (expand_heredoc, expand_tokens, expand_value, expand_word,
                     split_assignment)
from .heredoc import ReadLine, read_heredoc_body
from .jobs import CHILD_RESET_SIGNALS
from .errors import BreakLoop, ContinueLoop, ShellSyntaxError
from .lexer import SEMI, WORD, Token, lex
from .parser import (ForClause, IfClause, ListItem, WhileClause, Pipeline, Redirect, compound_open,
                     is_word, iter_commands, parse)
from .state import ShellState


SIGINT_STATUS = 128 + signal.SIGINT  # a command killed by Ctrl+C


@dataclass
class RedirectOp:
    # One of: <  <<<  1>  1>>  2>  2>>  &>  &>>  2>&1  1>&2  1>&1
    # A here-document becomes <<< with the body as its text.
    op: str
    arg: str = ""  # file name, or the text for <<<


def apply_redirects(ops: List[RedirectOp], table: Dict[int, object], opened: list) -> None:
    """Apply redirections left to right, the way a shell does.

    `table` maps fd 0, 1 and 2 to a handle: a file object or a raw fd number.
    A duplication such as 2>&1 copies what the other fd points to at that moment,
    so `> f 2>&1` sends both streams to f, while `2>&1 > f` sends only stdout there.
    Files opened here go into `opened`. The caller closes them."""
    def open_file(path: str, mode: str):
        f = open(path, mode)
        opened.append(f)
        return f

    for r in ops:
        op = r.op
        if op == "<":
            table[0] = open_file(r.arg, "r")
        elif op == "<<<":
            f = tempfile.TemporaryFile("w+")  # a file, not a pipe, so long text cannot block
            opened.append(f)
            f.write(r.arg)
            f.seek(0)
            table[0] = f
        elif op in ("1>", "1>>"):
            table[1] = open_file(r.arg, "a" if op.endswith(">>") else "w")
        elif op in ("2>", "2>>"):
            table[2] = open_file(r.arg, "a" if op.endswith(">>") else "w")
        elif op in ("&>", "&>>"):
            table[1] = table[2] = open_file(r.arg, "a" if op.endswith(">>") else "w")
        elif op == "2>&1":
            # subprocess has no fd to copy when stdout is a fresh pipe, so it uses STDOUT.
            table[2] = subprocess.STDOUT if table[1] == subprocess.PIPE else table[1]
        elif op == "1>&2":
            if table[2] != subprocess.STDOUT:
                table[1] = table[2]
        # 1>&1 changes nothing.


def exit_status(returncode: int) -> int:
    # Popen reports death by signal as a negative number. Shells use 128 + signal.
    return 128 + -returncode if returncode < 0 else returncode


class Executor:
    def __init__(self, state: ShellState):
        self.state = state

    # --- lines ---
    def run_line(self, line: str, read_line: Optional[ReadLine] = None) -> int:
        """Run one line. Here-document bodies are read from read_line, which returns
        the next input line or None at the end of input."""
        tokens = lex(line)
        while compound_open(tokens):  # an if block continues on the next lines
            more = read_line() if read_line is not None else None
            if more is None:
                raise ShellSyntaxError("unexpected end of file")
            extra = lex(more)
            last = tokens[-1]
            joins = last.kind == WORD and not is_word(last, ("then", "else", "elif", "if", "do", "while", "until"))
            if extra and joins:
                tokens.append(Token(SEMI, [(";", "plain")]))
            tokens.extend(extra)
        items = parse(expand_aliases(tokens, self.state.aliases))
        for cmd in iter_commands(items):
            for red in cmd.redirects:
                if red.op in ("<<", "<<-"):
                    red.body = read_heredoc_body(red.target.text, red.op == "<<-", read_line)
        return self.run_list(items)

    # --- lists ---
    def run_list(self, items: List[ListItem]) -> int:
        status = self.state.last_status
        for item in items:
            if item.op == "&&" and status != 0:
                continue
            if item.op == "||" and status == 0:
                continue
            try:
                node = item.pipeline
                if isinstance(node, IfClause):
                    status = self.run_if(node)
                elif isinstance(node, WhileClause):
                    status = self.run_while(node)
                elif isinstance(node, ForClause):
                    status = self.run_for(node)
                else:
                    status = self.run_pipeline(node)
            except ShellError as e:
                print(e, file=sys.stderr)
                status = 2
            self.state.last_status = status
        return status

    def run_if(self, node: IfClause) -> int:
        for cond, body in node.clauses:
            if self.run_list(cond) == 0:
                return self.run_list(body)
        if node.else_body is not None:
            return self.run_list(node.else_body)
        return 0

    def run_body(self, body: List[ListItem]) -> Optional[int]:
        """Run one pass of a loop. Returns the status, or None when the loop must stop.
        break and continue arrive as exceptions. Counts above 1 reach the outer loop."""
        try:
            return self.run_list(body)
        except BreakLoop as e:
            if e.levels > 1:
                raise BreakLoop(e.levels - 1)
            return None
        except ContinueLoop as e:
            if e.levels > 1:
                raise ContinueLoop(e.levels - 1)
            return self.state.last_status

    def run_while(self, node: WhileClause) -> int:
        status = 0
        self.state.loop_depth += 1
        try:
            while True:
                cond = self.run_list(node.cond)
                if cond == SIGINT_STATUS:
                    return cond  # Ctrl+C killed the command, so the loop stops too
                if (cond == 0) == node.until:
                    break
                result = self.run_body(node.body)
                if result is None:
                    return 0
                status = result
                if status == SIGINT_STATUS:
                    break
        finally:
            self.state.loop_depth -= 1
        return status

    def run_for(self, node: ForClause) -> int:
        status = 0
        values = expand_tokens(node.words, self.state)
        self.state.loop_depth += 1
        try:
            for value in values:
                self.state.set_variable(node.name, value)
                result = self.run_body(node.body)
                if result is None:
                    return 0
                status = result
                if status == SIGINT_STATUS:
                    break
        finally:
            self.state.loop_depth -= 1
        return status

    # --- pipelines ---
    def run_pipeline(self, pipeline: Pipeline) -> int:
        commands = pipeline.commands

        # Peel leading NAME=value words off each command.
        assigns: List[List[Tuple[str, Token]]] = []
        word_tokens: List[List[Token]] = []
        for cmd in commands:
            found: List[Tuple[str, Token]] = []
            rest = list(cmd.argv)
            while rest:
                parsed = split_assignment(rest[0])
                if parsed is None:
                    break
                found.append(parsed)
                rest.pop(0)
            assigns.append(found)
            word_tokens.append(rest)

        argvs = [expand_tokens(t, self.state) for t in word_tokens]
        envs: List[Optional[Dict[str, str]]] = [
            {name: expand_value(tok, self.state) for name, tok in found} or None
            for found in assigns
        ]
        ops = [self.resolve_redirects(c.redirects) for c in commands]

        try:
            if len(argvs) == 1:
                argv = argvs[0]
                if not argv:
                    # Only assignments and/or redirects. `> file` creates the file and
                    # `NAME=value` sets a shell variable.
                    opened: list = []
                    try:
                        apply_redirects(ops[0], {0: None, 1: None, 2: None}, opened)
                    finally:
                        for f in opened:
                            f.close()
                    for name, value in (envs[0] or {}).items():
                        self.state.set_variable(name, value)
                    return 0
                # A lone builtin runs in the shell itself, so cd, alias and exit work.
                if argv[0] in BUILTINS and (not pipeline.background or not compat.HAS_FORK):
                    return self.run_builtin(argv[0], argv[1:], ops[0], envs[0])
            if any(not argv for argv in argvs):
                return 0
            if compat.HAS_FORK:
                command = " | ".join(" ".join(a) for a in argvs)
                return self.run_forked(argvs, envs, ops, pipeline.background, command)
            return self.run_portable(argvs, envs, ops, pipeline.background)
        except OSError as e:
            # A redirect target could not be opened, or fork failed.
            print(f"{e.filename or 'error'}: {e.strerror}", file=sys.stderr)
            return 1

    def resolve_redirects(self, redirects: List[Redirect]) -> List[RedirectOp]:
        """Expand file names and here-strings. Runs in the shell, before any fork."""
        ops: List[RedirectOp] = []
        for red in redirects:
            if red.target is None:
                ops.append(RedirectOp({">&2": "1>&2", ">&1": "1>&1"}.get(red.op, red.op)))
            elif red.op == "<<<":
                ops.append(RedirectOp("<<<", expand_value(red.target, self.state) + "\n"))
            elif red.op in ("<<", "<<-"):
                # A quoted delimiter means the body is taken literally.
                body = red.body if red.target.quoted else expand_heredoc(red.body, self.state)
                ops.append(RedirectOp("<<<", body))
            else:
                names = expand_word(red.target, self.state)
                if len(names) != 1:
                    raise ShellError(f"{red.target.text}: ambiguous redirect")
                ops.append(RedirectOp({">": "1>", ">>": "1>>"}.get(red.op, red.op), names[0]))
        return ops

    # --- a lone builtin runs inside the shell process ---
    def run_builtin(self, name: str, args: List[str], ops: List[RedirectOp],
                    env: Optional[Dict[str, str]] = None) -> int:
        opened: list = []
        saved = {k: os.environ.get(k) for k in env} if env else {}
        try:
            table: Dict[int, object] = {0: sys.stdin, 1: sys.stdout, 2: sys.stderr}
            apply_redirects(ops, table, opened)
            if env:
                os.environ.update(env)  # NAME=value before a builtin lasts for that call only
            return BUILTINS[name](self.state, args, table[1], table[2])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            for f in opened:
                f.close()

    # --- Unix: fork, pipe, process groups ---
    def run_forked(self, cmds: List[List[str]], envs: List[Optional[Dict[str, str]]],
                   ops: List[List[RedirectOp]], background: bool, command: str) -> int:
        jc = self.state.jobs
        # Flush first so buffered text is not copied into every child.
        sys.stdout.flush()
        sys.stderr.flush()

        n = len(cmds)
        pipes: List[Tuple[int, int]] = []
        opened: list = []
        devnull: Optional[int] = None
        pids: List[int] = []
        pgid = 0
        try:
            for _ in range(n - 1):
                pipes.append(os.pipe())
            if background and not jc.interactive:
                devnull = os.open(os.devnull, os.O_RDONLY)  # background jobs get no input

            # Work out what fds 0, 1 and 2 of each command point to. Every redirect target
            # opens before anything starts, so one bad file name stops the whole pipeline.
            tables: List[Dict[int, int]] = []
            for i in range(n):
                table: Dict[int, object] = {
                    0: pipes[i - 1][0] if i else (devnull if devnull is not None else 0),
                    1: pipes[i][1] if i < n - 1 else 1,
                    2: 2,
                }
                apply_redirects(ops[i], table, opened)
                tables.append({fd: h if isinstance(h, int) else h.fileno()
                               for fd, h in table.items()})

            # Children close every pipe end and file they do not use, or readers
            # would never see end of input.
            inherited = {fd for pair in pipes for fd in pair}
            inherited.update(f.fileno() for f in opened)
            if devnull is not None:
                inherited.add(devnull)
            inherited = {fd for fd in inherited if fd > 2}

            for i, argv in enumerate(cmds):
                pid = os.fork()
                if pid == 0:
                    self._run_child(argv, tables[i], inherited, pgid,
                                    not background, envs[i])  # never returns

                # Parent. Both sides call setpgid so the group exists whoever runs first.
                if pgid == 0:
                    pgid = pid
                if jc.interactive:
                    try:
                        os.setpgid(pid, pgid)
                    except OSError:
                        pass
                pids.append(pid)
        finally:
            for pair in pipes:
                for fd in pair:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if devnull is not None:
                os.close(devnull)
            for f in opened:
                f.close()

        job = jc.add(pgid, pids, command, background)
        if background:
            jc.put_in_background(job)
            return 0
        status = jc.put_in_foreground(job)
        if status == 127 and n == 1:
            self.state.missing_command = cmds[0][0]  # lets the guide offer a hint
        return status

    def _run_child(self, argv: List[str], table: Dict[int, int],
                   inherited: Set[int], pgid: int, foreground: bool,
                   env: Optional[Dict[str, str]] = None) -> None:
        """Runs in the forked child. Never returns."""
        status = 1
        try:
            jc = self.state.jobs
            if jc.interactive:
                os.setpgid(0, pgid)  # pgid 0 makes this process the group leader
                if foreground:
                    try:
                        os.tcsetpgrp(jc.tty, os.getpgrp())
                    except OSError:
                        pass
            for sig in CHILD_RESET_SIGNALS:
                signal.signal(sig, signal.SIG_DFL)

            # table[t] is the parent fd that becomes fd t. When a source is itself fd 0, 1
            # or 2, copy it first. Otherwise `2>&1 > file` would lose the original stdout.
            sources: Dict[int, int] = {}
            copies: Set[int] = set()
            for target in (0, 1, 2):
                src = table[target]
                if src in (0, 1, 2) and src != target:
                    src = os.dup(src)
                    copies.add(src)
                sources[target] = src
            for target in (0, 1, 2):
                if sources[target] != target:
                    os.dup2(sources[target], target)
            for fd in inherited | copies:
                try:
                    os.close(fd)  # open pipe ends would keep readers from seeing EOF
                except OSError:
                    pass
            # Fresh file objects, so builtins and error messages reach fds 1 and 2
            # even if sys.stdout or sys.stderr were replaced.
            sys.stdout = os.fdopen(1, "w", closefd=False)
            sys.stderr = os.fdopen(2, "w", buffering=1, closefd=False)

            if argv[0] in BUILTINS:
                if env:
                    os.environ.update(env)  # this is a child, so no need to restore
                try:
                    status = BUILTINS[argv[0]](self.state, argv[1:], sys.stdout, sys.stderr)
                except ShellExit as e:
                    status = e.code
            elif env:
                os.execvpe(argv[0], argv, {**os.environ, **env})
            else:
                os.execvp(argv[0], argv)
        except FileNotFoundError:
            reason = "No such file or directory" if "/" in argv[0] else "command not found"
            try:
                print(f"{argv[0]}: {reason}", file=sys.stderr)
            except Exception:
                pass
            status = 127
        except OSError as e:
            try:
                print(f"{argv[0]}: {e.strerror}", file=sys.stderr)
            except Exception:
                pass
            status = 126
        except BaseException:
            status = 1
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            os._exit(status & 0xFF)

    # --- Windows and other platforms without fork ---
    def run_portable(self, cmds: List[List[str]], envs: List[Optional[Dict[str, str]]],
                     ops: List[List[RedirectOp]], background: bool) -> int:
        sys.stdout.flush()
        sys.stderr.flush()
        n = len(cmds)
        opened: list = []
        processes: List[subprocess.Popen] = []
        prev = None          # stdin for the next command
        last_failure = None  # status when the last command could not start
        try:
            # Open every redirect target first, so a bad file name starts nothing.
            tables: List[Dict[int, object]] = []
            for i in range(n):
                table: Dict[int, object] = {
                    0: None,  # None means the previous pipe, or the shell's own stdin
                    1: subprocess.PIPE if i < n - 1 else sys.stdout,
                    2: sys.stderr,
                }
                apply_redirects(ops[i], table, opened)
                tables.append(table)

            for i, cmd in enumerate(cmds):
                is_last = i == n - 1
                table = tables[i]
                try:
                    p = subprocess.Popen(
                        cmd,
                        stdin=table[0] if table[0] is not None else prev,
                        stdout=table[1],
                        stderr=table[2],
                        env={**os.environ, **envs[i]} if envs[i] else None,
                    )
                except (FileNotFoundError, PermissionError, NotADirectoryError) as e:
                    if isinstance(e, FileNotFoundError):
                        reason = ("No such file or directory" if "/" in cmd[0]
                                  else "command not found")
                        status = 127
                    else:
                        reason = "Permission denied"
                        status = 126
                    print(f"{cmd[0]}: {reason}", file=sys.stderr)
                    p, last_failure = None, (status if is_last else None)
                if hasattr(prev, "close"):
                    prev.close()
                if p is None:
                    prev = subprocess.DEVNULL  # next stage reads empty input
                else:
                    prev = p.stdout
                    processes.append(p)
        finally:
            if hasattr(prev, "close"):
                prev.close()
            for f in opened:
                f.close()

        if background:
            if processes:
                self.state.background_jobs.append(processes[-1])
                print(f"[{len(self.state.background_jobs)}] {processes[-1].pid}")
            return last_failure or 0

        try:
            for p in processes:
                p.wait()
        except KeyboardInterrupt:
            for p in processes:
                p.wait()
            print()
            return 130

        if last_failure is not None:
            return last_failure
        return exit_status(processes[-1].returncode)
