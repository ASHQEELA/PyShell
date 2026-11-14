"""Shared helpers.

End-to-end tests run the real shell in a subprocess with `python -m myshell -c SCRIPT`,
so stdout, stderr and the exit code are exactly what a user sees.
In-process tests use the `state` fixture."""
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

unix_only = pytest.mark.skipif(os.name == "nt", reason="needs Unix tools such as cat and seq")

PORTABLE_LAUNCHER = (
    "import sys\n"
    "from myshell import compat\n"
    "compat.HAS_FORK = False\n"
    "from myshell.cli import main\n"
    "sys.exit(main(sys.argv[1:]))\n"
)


@dataclass
class Result:
    out: str
    err: str
    code: int


def clean_env(home: Path, extra=None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "PS1")}
    env.update(HOME=str(home), TERM="dumb", PYTHONPATH=str(ROOT), MYSHELL_EDITOR="basic", COLUMNS="80")
    if extra:
        env.update(extra)
    return env


@pytest.fixture
def dirs(tmp_path):
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    return home, work


@pytest.fixture
def run_cli(dirs):
    """run_cli(args, cwd=None, env=None, portable=False) -> Result"""
    home, work = dirs

    def run(args, *, cwd=None, env=None, portable=False, timeout=30) -> Result:
        cmd = [sys.executable, "-c", PORTABLE_LAUNCHER, *args] if portable \
            else [sys.executable, "-m", "myshell", *args]
        p = subprocess.run(cmd, cwd=cwd or work, env=clean_env(home, env),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=timeout)
        return Result(p.stdout, p.stderr, p.returncode)

    return run


@pytest.fixture
def sh(run_cli, dirs):
    """sh(script) runs a script string. Lines run one after another, like a file."""
    home, work = dirs

    def run(script, **kw) -> Result:
        return run_cli(["-c", script], **kw)

    run.home = home
    run.work = work
    run.real_work = os.path.realpath(work)
    return run


@pytest.fixture
def state(monkeypatch, dirs):
    """A ShellState wired to an executor, with HOME pointing at a temp dir."""
    from myshell.executor import Executor
    from myshell.state import ShellState

    home, work = dirs
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    s = ShellState()
    s.executor = Executor(s)
    return s


class PtySession:
    """Drives the interactive shell through a pseudo terminal. Waits for text
    to appear instead of sleeping for a fixed time."""

    def __init__(self, home: Path, cwd: Path, env=None):
        import pty
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(cwd)
            os.execvpe(sys.executable, [sys.executable, "-m", "myshell"],
                       clean_env(home, env))
        self.buffer = ""

    def _read_some(self, timeout):
        import select
        r, _, _ = select.select([self.fd], [], [], timeout)
        if r:
            try:
                data = os.read(self.fd, 4096)
            except OSError:
                return False
            if data:
                self.buffer += data.decode(errors="replace").replace("\r", "")
                return True
        return False

    def expect(self, text, timeout=8.0):
        """Wait until text appears in the output not yet consumed. Returns that output."""
        end = time.time() + timeout
        while time.time() < end:
            if text in self.buffer:
                idx = self.buffer.index(text) + len(text)
                seen, self.buffer = self.buffer[:idx], self.buffer[idx:]
                return seen
            self._read_some(0.1)
        raise AssertionError(f"timed out waiting for {text!r}. Output so far: {self.buffer!r}")

    def send(self, text):
        os.write(self.fd, text.encode())

    def close(self):
        try:
            os.write(self.fd, b"exit\n")
        except OSError:
            pass
        end = time.time() + 5
        while time.time() < end:
            try:
                done, _ = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                return
            if done:
                return
            self._read_some(0.1)
        try:
            os.kill(self.pid, 9)
            os.waitpid(self.pid, 0)
        except (OSError, ChildProcessError):
            pass


@pytest.fixture
def pty_shell(dirs):
    home, work = dirs
    sessions = []

    def start(env=None, cwd=None):
        s = PtySession(home, cwd or home, env)
        sessions.append(s)
        return s

    yield start
    for s in sessions:
        s.close()
