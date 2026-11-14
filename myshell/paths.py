"""The home folder. HOME wins when it is set, because Windows ignores it otherwise."""
import os


def home_dir() -> str:
    return os.environ.get("HOME") or os.path.expanduser("~")


def expand_user(text: str) -> str:
    """Like os.path.expanduser, but a plain ~ or ~/ uses HOME when HOME is set."""
    home = os.environ.get("HOME")
    if home and (text == "~" or text.startswith("~/") or (os.sep != "/" and text.startswith("~" + os.sep))):
        return home + text[1:]
    return os.path.expanduser(text)


def home_file(name: str) -> str:
    return os.path.join(home_dir(), name)
