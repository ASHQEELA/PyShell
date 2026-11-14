import os

import pytest

from conftest import unix_only

from myshell.prompt import build_prompt, expand_prompt, short_path
from myshell.state import ShellState

CH = "#" if hasattr(os, "geteuid") and os.geteuid() == 0 else "$"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "proj" / "sub").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PS1", raising=False)
    monkeypatch.chdir(h)
    return h


@pytest.fixture
def st():
    return ShellState()


def mark(text):
    """The readline markers are left out on macOS."""
    from myshell import prompt
    return text if prompt.MARKERS else text.replace("\x01", "").replace("\x02", "")


class TestShortPath:
    def test_home_becomes_tilde(self, home):
        assert short_path(str(home)) == "~"
        assert short_path(f"{home}/proj/sub") == "~/proj/sub"

    def test_trailing_slash_in_home(self, home, monkeypatch):
        monkeypatch.setenv("HOME", f"{home}/")
        assert short_path(f"{home}/proj") == "~/proj"

    def test_a_folder_that_only_starts_like_home_is_left_alone(self, home, monkeypatch):
        monkeypatch.setenv("HOME", f"{home}/pro")
        assert short_path(f"{home}/proj") == f"{home}/proj"

    def test_no_home(self, home, monkeypatch):
        monkeypatch.setenv("HOME", "")
        assert short_path("/usr") == "/usr"


class TestEscapes:
    def test_directory(self, home, st):
        assert expand_prompt(r"\w", st) == "~"
        os.chdir(home / "proj" / "sub")
        assert expand_prompt(r"\w", st) == "~/proj/sub"
        assert expand_prompt(r"\W", st) == "sub"

    def test_basename_of_root_and_home(self, home, st):
        assert expand_prompt(r"\W", st) == "~"
        os.chdir("/")
        assert expand_prompt(r"\W", st) == "/"

    def test_status(self, st):
        st.last_status = 127
        assert expand_prompt(r"[\?]", st) == "[127]"

    def test_dollar_sign(self, st):
        assert expand_prompt(r"\$", st) == CH

    def test_user_and_host(self, st):
        assert expand_prompt(r"\u@\h", st).count("@") == 1

    def test_control_characters(self, st):
        assert expand_prompt(r"a\nb\e[0m", st) == "a\nb\x1b[0m"

    def test_color_wrappers_become_readline_markers(self, st):
        assert expand_prompt(r"\[x\]", st) == mark("\x01x\x02")

    def test_backslash_and_unknown_escapes(self, st):
        assert expand_prompt(r"\\ \q", st) == "\\ \\q"

    def test_lone_trailing_backslash(self, st):
        assert expand_prompt("end\\", st) == "end\\"

    def test_job_count(self, st):
        assert expand_prompt(r"\j", st) == "0"

    @unix_only
    def test_deleted_directory_does_not_crash(self, tmp_path, monkeypatch, st):
        gone = tmp_path / "gone"
        gone.mkdir()
        monkeypatch.chdir(gone)
        gone.rmdir()
        monkeypatch.setenv("PWD", str(gone))
        assert expand_prompt(r"\w", st) == str(gone)


class TestDefaultPrompt:
    def test_plain_on_a_dumb_terminal(self, home, st, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        assert build_prompt(st) == f"~ {CH} "

    def test_failure_adds_the_status(self, home, st, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        st.last_status = 2
        assert build_prompt(st) == f"[2] ~ {CH} "

    def test_color_terminal(self, home, st, monkeypatch):
        monkeypatch.setenv("TERM", "xterm")
        assert build_prompt(st) == mark(f"\x01\x1b[1;32m\x02~\x01\x1b[0m\x02 {CH} ")

    def test_failure_is_red_on_a_color_terminal(self, home, st, monkeypatch):
        monkeypatch.setenv("TERM", "xterm")
        st.last_status = 3
        assert build_prompt(st).startswith(mark("\x01\x1b[1;31m\x02[3]"))

    def test_no_color_turns_color_off(self, home, st, monkeypatch):
        monkeypatch.setenv("TERM", "xterm")
        monkeypatch.setenv("NO_COLOR", "1")
        assert "\x1b" not in build_prompt(st)


class TestCustomPrompt:
    def test_ps1_replaces_the_default(self, home, st):
        st.set_variable("PS1", r"\W> ")
        assert build_prompt(st) == "~> "

    def test_ps1_ignores_the_status_prefix(self, home, st):
        st.set_variable("PS1", "> ")
        st.last_status = 1
        assert build_prompt(st) == "> "

    def test_unsetting_ps1_restores_the_default(self, home, st, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        st.set_variable("PS1", "X ")
        st.unset_variable("PS1")
        assert build_prompt(st) == f"~ {CH} "
