import os
import readline
import stat

import pytest

from myshell.completion import Completer, escape_word, unescape_word


@pytest.fixture
def tree(state, dirs):
    _, work = dirs
    (work / "alpha.txt").write_text("")
    (work / "alpine").mkdir()
    (work / "beta.txt").write_text("")
    (work / ".hidden").write_text("")
    (work / "my file").write_text("")
    return work


@pytest.fixture
def comp(state):
    return Completer(state)


def set_line(monkeypatch, line, begin):
    monkeypatch.setattr(readline, "get_line_buffer", lambda: line)
    monkeypatch.setattr(readline, "get_begidx", lambda: begin)


class TestPaths:
    def test_prefix_match_marks_directories(self, tree, comp):
        assert comp.path_matches("al") == ["alpha.txt", "alpine/"]

    def test_hidden_files_need_a_dot(self, tree, comp):
        assert ".hidden" not in comp.path_matches("")
        assert comp.path_matches(".h") == [".hidden"]

    def test_spaces_are_escaped(self, tree, comp):
        assert comp.path_matches("my") == ["my\\ file"]

    def test_escaped_input_is_understood(self, tree, comp):
        assert comp.path_matches("my\\ f") == ["my\\ file"]

    def test_subdirectory(self, tree, comp):
        (tree / "alpine" / "inner.txt").write_text("")
        assert comp.path_matches("alpine/") == ["alpine/inner.txt"]

    def test_tilde(self, tree, comp, dirs):
        home, _ = dirs
        (home / "notes.txt").write_text("")
        assert comp.path_matches("~") == ["~/"]
        assert comp.path_matches("~/no") == ["~/notes.txt"]

    def test_missing_directory(self, tree, comp):
        assert comp.path_matches("nowhere/x") == []


class TestCommands:
    @pytest.fixture
    def bin_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "bin"
        d.mkdir()
        tool = d / "mytool"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        (d / "mydata").write_text("not executable")
        monkeypatch.setenv("PATH", str(d))
        return d

    def test_executables_in_path(self, bin_dir, comp):
        assert comp.command_matches("my") == ["mytool"]

    def test_builtins_and_aliases_are_included(self, bin_dir, comp, state):
        state.aliases["cdd"] = "cd /"
        assert comp.command_matches("cd") == ["cd", "cdd"]

    def test_unknown_directories_in_path_are_skipped(self, bin_dir, comp, monkeypatch):
        monkeypatch.setenv("PATH", f"/no/such/dir{os.pathsep}{bin_dir}")
        assert comp.command_matches("my") == ["mytool"]


class TestVariables:
    def test_environment_and_shell_variables(self, comp, state, monkeypatch):
        monkeypatch.setenv("ZZ_ENV", "1")
        state.variables["ZZ_SHELL"] = "2"
        assert comp.variable_matches("$ZZ_") == ["$ZZ_ENV", "$ZZ_SHELL"]


class TestWhatGetsCompleted:
    def test_first_word_is_a_command(self, tree, comp, monkeypatch):
        set_line(monkeypatch, "ec", 0)
        assert "echo" in comp.candidates("ec")

    @pytest.mark.parametrize("line", ["echo hi | ec", "true && ec", "true || ec", "true; ec",
                                      "sleep 1 & ec"])
    def test_word_after_an_operator_is_a_command(self, tree, comp, monkeypatch, line):
        set_line(monkeypatch, line, len(line) - 2)
        assert "echo" in comp.candidates("ec")

    def test_argument_is_a_path(self, tree, comp, monkeypatch):
        set_line(monkeypatch, "cat al", 4)
        assert comp.candidates("al") == ["alpha.txt", "alpine/"]

    @pytest.mark.parametrize("line", ["echo x > al", "cat < al", "echo x 2> al"])
    def test_redirect_target_is_a_path(self, tree, comp, monkeypatch, line):
        set_line(monkeypatch, line, len(line) - 2)
        assert comp.candidates("al") == ["alpha.txt", "alpine/"]

    def test_dollar_completes_variables(self, tree, comp, monkeypatch, state):
        state.variables["ZZ_ONLY"] = "1"
        set_line(monkeypatch, "echo $ZZ_O", 5)
        assert comp.candidates("$ZZ_O") == ["$ZZ_ONLY"]

    def test_readline_protocol(self, tree, comp, monkeypatch):
        set_line(monkeypatch, "cat al", 4)
        assert comp("al", 0) == "alpha.txt"
        assert comp("al", 1) == "alpine/"
        assert comp("al", 2) is None


def test_escape_round_trip():
    for text in ["plain", "with space", "a&b", "x;y", "quo'te", 'dq"x', "paren(s)", "$var"]:
        assert unescape_word(escape_word(text)) == text
