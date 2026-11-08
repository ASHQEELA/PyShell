"""Platform checks.

Other modules read compat.HAS_FORK when they run, never at import time,
so tests can switch the portable code path on and off."""
import os

try:
    import termios
except ImportError:  # Windows
    termios = None

# True on Unix. The shell then runs commands with fork, pipe and process groups.
# Without it the shell falls back to the subprocess module.
HAS_FORK = all(hasattr(os, n) for n in ("fork", "setpgid", "killpg", "tcsetpgrp"))
