"""Job control for Unix: process groups, the terminal, fg, bg and wait."""
import os
import signal
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from . import compat
from .errors import ShellError


# Signals the shell ignores or handles itself. Children must restore the defaults.
CHILD_RESET_SIGNALS = [
    getattr(signal, n) for n in
    ("SIGINT", "SIGQUIT", "SIGTSTP", "SIGTTIN", "SIGTTOU", "SIGCHLD", "SIGPIPE", "SIGXFSZ")
    if hasattr(signal, n)
]


@dataclass
class Job:
    pgid: int
    pids: List[int]
    command: str
    status: Dict[int, Optional[int]]        # pid -> exit status, None while alive
    number: int = 0                         # assigned when the job goes to the background
    background: bool = False
    notified: bool = False
    term_signal: Optional[int] = None       # signal that killed the last process
    stopped_pids: Set[int] = field(default_factory=set)
    tmodes: Optional[list] = None           # terminal modes saved when the job stopped

    @property
    def completed(self) -> bool:
        return all(s is not None for s in self.status.values())

    @property
    def stopped(self) -> bool:
        if self.completed:
            return False
        return all(self.status[p] is not None or p in self.stopped_pids for p in self.pids)

    @property
    def last_status(self) -> int:
        return self.status[self.pids[-1]] or 0

    def state_text(self) -> str:
        if self.completed:
            if self.term_signal is not None:
                return signal.strsignal(self.term_signal) or f"Signal {self.term_signal}"
            return "Done" if self.last_status == 0 else f"Exit {self.last_status}"
        return "Stopped" if self.stopped else "Running"


class JobControl:
    def __init__(self):
        self.jobs: List[Job] = []
        self.interactive = False
        self.tty = -1
        self.shell_pgid = 0
        self.shell_tmodes = None

    # --- terminal setup ---
    def init_terminal(self) -> None:
        """Take control of the terminal. Skipped when stdin is not a terminal."""
        if not compat.HAS_FORK or compat.termios is None:
            return
        try:
            tty = sys.stdin.fileno()
            if not os.isatty(tty):
                return
            # If the shell was started in the background, stop until it is foreground.
            while os.tcgetpgrp(tty) != os.getpgrp():
                os.killpg(os.getpgrp(), signal.SIGTTIN)
            for sig in (signal.SIGQUIT, signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU):
                signal.signal(sig, signal.SIG_IGN)
            pid = os.getpid()
            try:
                os.setpgid(pid, pid)  # fails with EPERM for a session leader, which is fine
            except OSError:
                pass
            self.shell_pgid = os.getpgrp()
            os.tcsetpgrp(tty, self.shell_pgid)
            self.shell_tmodes = compat.termios.tcgetattr(tty)
            self.tty = tty
            self.interactive = True
        except (OSError, compat.termios.error):
            self.interactive = False

    # --- table ---
    def add(self, pgid: int, pids: List[int], command: str, background: bool) -> Job:
        job = Job(pgid, pids, command, {p: None for p in pids}, background=background)
        self.jobs.append(job)
        return job

    def remove(self, job: Job) -> None:
        if job in self.jobs:
            self.jobs.remove(job)

    def assign_number(self, job: Job) -> None:
        if not job.number:
            job.number = max([j.number for j in self.jobs], default=0) + 1

    def numbered(self) -> List[Job]:
        return [j for j in self.jobs if j.number]

    def marker(self, job: Job) -> str:
        nums = self.numbered()
        if nums and job is nums[-1]:
            return "+"
        if len(nums) > 1 and job is nums[-2]:
            return "-"
        return " "

    def format(self, job: Job) -> str:
        suffix = " &" if job.background and not job.completed and not job.stopped else ""
        return f"[{job.number}]{self.marker(job)}  {job.state_text():<24}{job.command}{suffix}"

    def find(self, spec: Optional[str]) -> Job:
        nums = self.numbered()
        if spec is None or spec in ("%", "%%", "%+"):
            if not nums:
                raise ShellError("no current job")
            return nums[-1]
        if spec == "%-":
            if len(nums) < 2:
                raise ShellError("no previous job")
            return nums[-2]
        text = spec[1:] if spec.startswith("%") else spec
        if text.isdigit():
            for j in nums:
                if j.number == int(text):
                    return j
        raise ShellError(f"{spec}: no such job")

    # --- status tracking ---
    def update(self, pid: int, st: int) -> None:
        for job in self.jobs:
            if pid not in job.status:
                continue
            if os.WIFSTOPPED(st):
                job.stopped_pids.add(pid)
                job.notified = False
            elif os.WIFSIGNALED(st):
                job.status[pid] = 128 + os.WTERMSIG(st)
                if pid == job.pids[-1]:
                    job.term_signal = os.WTERMSIG(st)
            else:
                job.status[pid] = os.WEXITSTATUS(st)
            return

    def refresh(self) -> None:
        """Collect finished or stopped children without blocking."""
        if not compat.HAS_FORK:
            return
        while True:
            try:
                pid, st = os.waitpid(-1, os.WUNTRACED | os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            self.update(pid, st)

    def wait_for(self, job: Job, interruptible: bool = False) -> bool:
        """Block until the job completes or stops. Returns True if interrupted."""
        while not job.completed and not job.stopped:
            try:
                pid, st = os.waitpid(-1, os.WUNTRACED)
            except ChildProcessError:
                for p in job.pids:  # children were reaped elsewhere
                    if job.status[p] is None:
                        job.status[p] = 0
                break
            except KeyboardInterrupt:
                # Without a terminal the shell shares the job's process group, so it
                # receives Ctrl+C too. The job dies from the same signal.
                if interruptible:
                    return True
                continue
            self.update(pid, st)
        return False

    def notify(self) -> None:
        """Report finished and stopped jobs. Called before each prompt."""
        self.refresh()
        for job in self.jobs[:]:
            if job.completed:
                if job.background and job.number:
                    print(self.format(job))
                self.jobs.remove(job)
            elif job.stopped and job.number and not job.notified:
                print(self.format(job))
                job.notified = True

    # --- moving jobs between foreground and background ---
    def _continue(self, job: Job) -> None:
        job.stopped_pids.clear()
        job.notified = False
        try:
            os.killpg(job.pgid, signal.SIGCONT)
        except OSError:
            pass

    def put_in_foreground(self, job: Job, cont: bool = False) -> int:
        job.background = False
        if self.interactive:
            try:
                os.tcsetpgrp(self.tty, job.pgid)
            except OSError:
                pass
        if cont:
            if self.interactive and job.tmodes:
                try:
                    compat.termios.tcsetattr(self.tty, compat.termios.TCSADRAIN, job.tmodes)
                except compat.termios.error:
                    pass
            self._continue(job)
        self.wait_for(job)
        if self.interactive:
            try:
                os.tcsetpgrp(self.tty, self.shell_pgid)
                if job.stopped:
                    job.tmodes = compat.termios.tcgetattr(self.tty)
                compat.termios.tcsetattr(self.tty, compat.termios.TCSADRAIN, self.shell_tmodes)
            except (OSError, compat.termios.error):
                pass

        if job.completed:
            self.remove(job)
            sig = job.term_signal
            # These notices go to stderr so they never end up in captured output.
            if sig == signal.SIGINT:
                if self.interactive:
                    print(file=sys.stderr)
            elif sig is not None and sig != signal.SIGPIPE:
                print(signal.strsignal(sig), file=sys.stderr)
            return job.last_status

        # The job stopped (Ctrl+Z). Keep it in the table.
        self.assign_number(job)
        job.notified = True
        print()
        print(self.format(job))
        return 128 + signal.SIGTSTP

    def put_in_background(self, job: Job, cont: bool = False) -> None:
        job.background = True
        self.assign_number(job)
        if cont:
            self._continue(job)
            print(f"[{job.number}]{self.marker(job)} {job.command} &")
        else:
            print(f"[{job.number}] {job.pids[-1]}")
