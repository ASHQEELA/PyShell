"""Entry point.

    python -m myshell              interactive shell
    python -m myshell -c "cmd"     run a command string and exit
    python -m myshell script.sh    run a script file and exit
"""
import os
import sys
import time
from typing import List, Optional

from . import history
from .builtins import run_script
from .completion import setup_readline
from .errors import ShellError, ShellExit
from .executor import Executor
from .guide import greeting, guide_enabled, missing_hint, say, syntax_hint
from .editor import make_reader
from .prompt import build_continuation_prompt, build_prompt, slow_notice
from .state import ShellState


def new_shell() -> ShellState:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 6000))  # deep shell functions need it
    if not os.environ.get("HOME") and os.environ.get("USERPROFILE"):
        os.environ["HOME"] = os.environ["USERPROFILE"]  # Windows has no HOME
    state = ShellState()
    state.executor = Executor(state)
    try:
        os.environ["PWD"] = os.getcwd()
    except OSError:
        pass
    return state


def run_exit_trap(state: ShellState) -> None:
    """Run the command set with `trap '...' EXIT`, once."""
    cmd = state.traps.pop("EXIT", None)
    if cmd:
        try:
            state.execute(cmd)
        except (ShellExit, ShellError):
            pass


def run_noninteractive(state: ShellState, text: str, label: str) -> int:
    """Run a command string or script. No prompt, no rc file, no job control."""
    try:
        run_script(state, text, label, sys.stderr)
        code = state.last_status
    except ShellExit as e:
        code = e.code
    except KeyboardInterrupt:
        code = 130
    state.last_status = code
    run_exit_trap(state)
    return code


def run_interactive(state: ShellState) -> int:
    state.jobs.init_terminal()
    state.load_history()
    setup_readline(state)
    reader = make_reader(state)  # None means plain input() with readline
    code = 0
    try:
        if os.path.isfile(state.rc_file):
            from .builtins import source_file
            source_file(state, state.rc_file, sys.stderr)
            state.last_status = 0
        interactive = sys.stdin.isatty()  # scripts piped in keep the plain "$ " prompt
        if interactive and guide_enabled(state):
            greeting(state, sys.stdout)
        while True:
            state.reap_jobs()
            try:
                try:
                    prompt = build_prompt(state) if interactive else "$ "
                except Exception:
                    prompt = "$ "
                line = reader.read(prompt) if reader else input(prompt)
            except EOFError:
                print("exit")
                code = state.last_status
                break
            except KeyboardInterrupt:
                print()
                continue
            if not line.strip():
                continue
            if not interactive or not history.records_input():
                history.add(line)  # otherwise input() records the line itself

            def read_more() -> Optional[str]:
                try:
                    text = build_continuation_prompt(state) if interactive else ""
                    return reader.read_continuation(text) if reader else input(text)
                except EOFError:
                    return None
            state.missing_command = None
            started = time.monotonic()
            try:
                state.execute(line, read_more)
            except ShellError as e:
                print(e, file=sys.stderr)
                state.last_status = 2
                if interactive and guide_enabled(state):
                    hint = syntax_hint(str(e))
                    if hint:
                        say(hint, "worried")
            except KeyboardInterrupt:
                print()
            state.last_duration = time.monotonic() - started
            if state.pending_traps:
                state.run_pending_traps()
            if interactive and line.split()[0] != "guide":
                notice = slow_notice(state)
                if notice:
                    print(notice)
            if interactive and state.missing_command and guide_enabled(state):
                hint = missing_hint(state.missing_command, state)
                if hint:
                    say(hint, "worried")
    except ShellExit as e:
        code = e.code
    state.last_status = code
    run_exit_trap(state)
    state.save_history()
    return code


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    state = new_shell()
    if argv and argv[0] == "-c":
        if len(argv) < 2:
            print("myshell: -c: option requires an argument", file=sys.stderr)
            return 2
        if len(argv) > 2:  # myshell -c 'cmd' NAME ARG...  sets $0 and $1, $2, ...
            state.script_name = argv[2]
            state.positional = list(argv[3:])
        return run_noninteractive(state, argv[1], "-c")
    if argv:
        try:
            with open(argv[0]) as f:
                text = f.read()
        except OSError as e:
            print(f"myshell: {argv[0]}: {e.strerror}", file=sys.stderr)
            return 127
        state.script_name = argv[0]
        state.positional = list(argv[1:])
        return run_noninteractive(state, text, argv[0])
    return run_interactive(state)
