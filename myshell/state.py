"""Shell state: aliases, variables, jobs, history and the last exit status."""
import os
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

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
