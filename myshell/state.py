"""Shell state: aliases, variables, jobs, history and the last exit status."""
import os
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set, TextIO

from . import history
from .jobs import JobControl

if TYPE_CHECKING:
    from .executor import Executor


@dataclass
class ShellState:
    aliases: Dict[str, str] = field(default_factory=dict)
    jobs: JobControl = field(default_factory=JobControl)
    background_jobs: List[subprocess.Popen] = field(default_factory=list)  # portable path only
    history_file: str = os.path.expanduser("~/.custom_shell_history")
    rc_file: str = os.path.expanduser("~/.myshellrc")
    last_status: int = 0
    variables: Dict[str, str] = field(default_factory=dict)  # shell variables, not exported
    executor: Optional["Executor"] = None
    missing_command: Optional[str] = None  # set when a lone command was not found
    loop_depth: int = 0  # loops running right now. break and continue need one
    stdin: Optional[TextIO] = None  # input stream for the builtin that runs now. None means sys.stdin
    functions: Dict[str, list] = field(default_factory=dict)  # name -> body (a list of ListItem)
    positional: List[str] = field(default_factory=list)  # $1, $2, ... of the current function or script
    script_name: str = "myshell"  # $0
    func_depth: int = 0  # functions and sourced files running right now. return needs one
    local_frames: List[Dict[str, Optional[str]]] = field(default_factory=list)  # saved values for `local`
    options: Set[str] = field(default_factory=set)  # set -e, -u, -x as "errexit", "nounset", "xtrace"
    no_errexit: int = 0  # above 0 while a condition runs, where a failure must not stop the script
    traps: Dict[str, str] = field(default_factory=dict)  # signal name or EXIT -> command text
    pending_traps: List[str] = field(default_factory=list)  # signals caught, not yet handled
    keep_going: bool = False  # tests and the guide set this so an expansion error never ends the shell
    last_duration: float = 0.0  # seconds the last interactive command took
    progress_file: str = field(default_factory=lambda: os.path.expanduser("~/.myshell_progress.json"))

    def input_stream(self) -> TextIO:
        """Where a builtin reads its input: a redirect or pipe if there is one, else the terminal."""
        import sys
        return self.stdin if self.stdin is not None else sys.stdin

    # --- variables ---
    # Exported variables live in os.environ, so child processes inherit them.
    def get_variable(self, name: str) -> Optional[str]:
        if name in self.variables:
            return self.variables[name]
        return os.environ.get(name)

    def set_variable(self, name: str, value: str) -> None:
        if name in os.environ:
            os.environ[name] = value  # an exported variable stays exported
        else:
            self.variables[name] = value

    def export_variable(self, name: str, value: Optional[str] = None) -> None:
        if value is None:
            if name in self.variables:
                os.environ[name] = self.variables.pop(name)
        else:
            self.variables.pop(name, None)
            os.environ[name] = value

    def unset_variable(self, name: str) -> None:
        self.variables.pop(name, None)
        os.environ.pop(name, None)

    def execute(self, line: str, read_line: Optional[Callable[[], Optional[str]]] = None) -> int:
        """Run one line in this shell. Used by source, scripts and $(...).
        read_line supplies the lines of a here-document body."""
        return self.executor.run_line(line, read_line)

    def run_pending_traps(self) -> None:
        """Run the trap commands for signals caught since the last check."""
        while self.pending_traps:
            cmd = self.traps.get(self.pending_traps.pop(0))
            if cmd:
                saved = self.last_status
                try:
                    self.execute(cmd)
                finally:
                    self.last_status = saved

    def reap_jobs(self) -> None:
        self.jobs.notify()
        for job in self.background_jobs[:]:
            if job.poll() is not None:
                print(f"[Done] {job.args}")
                self.background_jobs.remove(job)

    def load_history(self) -> None:
        history.set_limit()
        try:
            history.read(self.history_file)
        except (OSError, ValueError):
            pass

    def save_history(self) -> None:
        try:
            history.write(self.history_file)
        except Exception:
            pass
