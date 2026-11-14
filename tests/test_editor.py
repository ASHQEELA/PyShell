import os
import stat

import pytest

from conftest import unix_only

pytest.importorskip("prompt_toolkit")

from prompt_toolkit.document import Document  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402

from myshell import editor, history  # noqa: E402


@pytest.fixture(autouse=True)
def own_history(monkeypatch):
    monkeypatch.setattr(history, "native", False)
    monkeypatch.setattr(history, "_entries", [])
    monkeypatch.delenv("MYSHELL_EDITOR", raising=False)


@pytest.fixture
def pipe():
    with create_pipe_input() as p:
        yield p


@pytest.fixture
def reader(state, pipe):
    return editor.make_reader(state, input=pipe, output=DummyOutput(), force=True)


def test_plain_line(reader, pipe):
    pipe.send_text("echo hi\r")
    assert reader.read("$ ") == "echo hi"


def test_prompt_with_readline_markers(reader, pipe):
    pipe.send_text("x\r")
    assert reader.read("\x01\x1b[1;32m\x02dir\x01\x1b[0m\x02 $ ") == "x"


def test_right_arrow_accepts_suggestion(reader, pipe):
    history.add("echo one")
    history.add("git status --short")
    pipe.send_text("git s\x1b[C\r")
    assert reader.read("$ ") == "git status --short"


def test_end_key_accepts_suggestion(reader, pipe):
    history.add("make clean all")
    pipe.send_text("make c\x1b[F\r")
    assert reader.read("$ ") == "make clean all"


def test_suggestion_sees_lines_added_between_prompts(reader, pipe):
    pipe.send_text("first\r")
    reader.read("$ ")
    history.add("grep needle haystack")
    pipe.send_text("grep n\x1b[C\r")
    assert reader.read("$ ") == "grep needle haystack"


def test_ctrl_r_search(reader, pipe):
    for line in ("ls -l", "grep needle file", "echo done"):
        history.add(line)
    pipe.send_text("\x12needle\r\r")
    assert reader.read("$ ") == "grep needle file"


def test_ctrl_d_on_empty_line(reader, pipe):
    pipe.send_text("\x04")
    with pytest.raises(EOFError):
        reader.read("$ ")


def test_ctrl_c(reader, pipe):
    pipe.send_text("abc\x03")
    with pytest.raises(KeyboardInterrupt):
        reader.read("$ ")


def test_continuation_is_not_suggested_or_recorded(reader, pipe):
    history.add("secret body")
    pipe.send_text("secret\x1b[C\r")
    assert reader.read_continuation("> ") == "secret"
    pipe.send_text("secret\x1b[C\r")
    assert reader.read("$ ") == "secret body"  # suggestions are back
    assert history.entries() == ["secret body"]


def test_make_reader_installs_builtin_history(state, pipe):
    editor.make_reader(state, input=pipe, output=DummyOutput(), force=True)
    assert history.records_input() is False


class TestMakeReaderGating:
    def test_not_a_tty(self, state, monkeypatch):
        monkeypatch.setenv("TERM", "xterm")
        assert editor.make_reader(state) is None  # pytest's stdin is not a tty

    def test_dumb_term(self, state, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        assert editor.make_reader(state) is None

    def test_empty_term(self, state, monkeypatch):
        monkeypatch.setenv("TERM", "")
        assert editor.make_reader(state) is None

    def test_basic_wins_over_force(self, state, monkeypatch):
        monkeypatch.setenv("MYSHELL_EDITOR", "basic")
        assert editor.make_reader(state, force=True) is None

    def test_none_leaves_history_alone(self, state, monkeypatch):
        monkeypatch.setattr(history, "native", True)
        monkeypatch.setenv("MYSHELL_EDITOR", "basic")
        editor.make_reader(state)
        assert history.native is True

    def test_fancy_skips_tty_check(self, state, pipe, monkeypatch):
        monkeypatch.setenv("MYSHELL_EDITOR", "fancy")
        monkeypatch.setenv("TERM", "dumb")
        assert editor.make_reader(state, input=pipe, output=DummyOutput()) is not None

    def test_missing_prompt_toolkit(self, state, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "prompt_toolkit", None)
        assert editor.make_reader(state, force=True) is None


def test_strip_markers():
    assert editor.strip_markers("\x01\x1b[0m\x02$ ") == "\x1b[0m$ "


class TestCompleterAdapter:
    def complete(self, reader, text):
        doc = Document(text, len(text))
        return [(c.text, c.start_position) for c in
                reader.completer.get_completions(doc, None)]

    def test_command_and_path(self, reader, state, dirs):
        _, work = dirs
        (work / "alpha.txt").write_text("")
        got = self.complete(reader, "cat al")
        assert got == [("alpha.txt", -2)]
        assert ("echo", -2) in self.complete(reader, "ls | ec")

    def test_functions_are_command_names(self, reader, state):
        state.functions["zzfunc"] = []
        assert ("zzfunc", -3) in self.complete(reader, "zzf")

    def test_no_completion_for_empty_dir_match(self, reader):
        assert self.complete(reader, "cat /no/such/dir/x") == []


class TestLexerAdapter:
    def lex(self, reader, text):
        get_line = reader.lexer.lex_document(Document(text))
        return [get_line(i) for i in range(text.count("\n") + 1)]

    def test_styles_and_roundtrip(self, reader, state):
        lines = self.lex(reader, 'echo "a $X" | nosuch_cmd_zz')
        flat = lines[0]
        assert "".join(t for _, t in flat) == 'echo "a $X" | nosuch_cmd_zz'
        assert ("class:command", "echo") in flat
        assert ("class:variable", "$X") in flat
        assert ("class:badcommand", "nosuch_cmd_zz") in flat

    def test_multiline_split(self, reader):
        lines = self.lex(reader, "echo a\nls")
        assert len(lines) == 2
        assert "".join(t for _, t in lines[0]) == "echo a"
        assert "".join(t for _, t in lines[1]) == "ls"

    def test_out_of_range_line(self, reader):
        assert reader.lexer.lex_document(Document("x"))(5) == []


class TestKindOf:
    @unix_only
    def test_builtin_alias_function_path(self, state, tmp_path, monkeypatch):
        d = tmp_path / "bin"
        d.mkdir()
        tool = d / "mytool"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", str(d))
        state.aliases["ll"] = "ls -l"
        state.functions["fn"] = []
        kind_of = editor.make_kind_of(state)
        assert kind_of("cd") == "command" and kind_of("ll") == "command"
        assert kind_of("fn") == "command" and kind_of("mytool") == "command"
        assert kind_of("nothing_here") == "unknown"

    @unix_only
    def test_cache_follows_path(self, state, tmp_path, monkeypatch):
        d = tmp_path / "bin"
        d.mkdir()
        monkeypatch.setenv("PATH", str(d))
        kind_of = editor.make_kind_of(state)
        assert kind_of("later") == "unknown"
        tool = d / "later"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        assert kind_of("later") == "unknown"  # cached for this PATH
        monkeypatch.setenv("PATH", str(d) + os.pathsep + "/nonexistent")
        assert kind_of("later") == "command"
