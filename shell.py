#!/usr/bin/env python3
"""Launcher, so `python shell.py` keeps working. The code lives in the myshell package."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from myshell.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
