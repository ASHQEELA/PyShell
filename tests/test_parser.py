import pytest

from myshell.errors import ShellSyntaxError
from myshell.lexer import lex
from myshell.parser import parse


def tree(line):
    return parse(lex(line))


def argv(command):
    return [t.text for t in command.argv]


def test_empty_line_gives_an_empty_tree():
    assert tree("") == []
    assert tree("# comment") == []


def test_single_command():
    items = tree("echo hello world")
    assert len(items) == 1
    assert items[0].op == ";"
    assert argv(items[0].pipeline.commands[0]) == ["echo", "hello", "world"]


def test_pipeline_has_one_command_per_stage():
    pipeline = tree("a x | b | c y z")[0].pipeline
    assert [argv(c) for c in pipeline.commands] == [["a", "x"], ["b"], ["c", "y", "z"]]


def test_list_operators_link_items_to_the_one_before():
    items = tree("a && b || c ; d")
    assert [i.op for i in items] == [";", "&&", "||", ";"]
    assert [argv(i.pipeline.commands[0]) for i in items] == [["a"], ["b"], ["c"], ["d"]]


def test_pipe_binds_tighter_than_and_or():
    items = tree("a | b && c | d")
    assert [len(i.pipeline.commands) for i in items] == [2, 2]


class TestBackground:
    def test_trailing_ampersand(self):
        items = tree("sleep 1 &")
        assert len(items) == 1
        assert items[0].pipeline.background

    def test_background_then_another_command(self):
        items = tree("a & b")
        assert [i.pipeline.background for i in items] == [True, False]
        assert items[1].op == ";"

    def test_whole_pipeline_goes_to_the_background(self):
        item = tree("a | b &")[0]
        assert item.pipeline.background
        assert len(item.pipeline.commands) == 2


class TestRedirects:
    def test_redirect_is_collected_and_not_part_of_argv(self):
        command = tree("echo hi > out.txt")[0].pipeline.commands[0]
        assert argv(command) == ["echo", "hi"]
        assert [(r.op, r.target.text) for r in command.redirects] == [(">", "out.txt")]

    def test_several_redirects_keep_their_order(self):
        command = tree("cmd < in > out 2> err")[0].pipeline.commands[0]
        assert [r.op for r in command.redirects] == ["<", ">", "2>"]

    def test_duplication_has_no_target(self):
        command = tree("cmd 2>&1")[0].pipeline.commands[0]
        assert [(r.op, r.target) for r in command.redirects] == [("2>&1", None)]

    def test_here_string(self):
        command = tree("cat <<< hello")[0].pipeline.commands[0]
        assert [(r.op, r.target.text) for r in command.redirects] == [("<<<", "hello")]

    def test_redirect_before_the_command_word(self):
        command = tree("> out echo hi")[0].pipeline.commands[0]
        assert argv(command) == ["echo", "hi"]
        assert len(command.redirects) == 1

    def test_redirect_alone_is_a_command(self):
        command = tree("> made.txt")[0].pipeline.commands[0]
        assert command.argv == []
        assert len(command.redirects) == 1

    def test_each_stage_has_its_own_redirects(self):
        stages = tree("a > f | b < g")[0].pipeline.commands
        assert [[r.op for r in s.redirects] for s in stages] == [[">"], ["<"]]


class TestSyntaxErrors:
    @pytest.mark.parametrize("line, message", [
        ("echo a &&", "unexpected end of line"),
        ("echo a ||", "unexpected end of line"),
        ("&& echo a", "unexpected token `&&'"),
        ("echo a | | cat", "unexpected token `|'"),
        ("echo a |", "unexpected end of line"),
        ("| echo a", "unexpected token `|'"),
        ("; echo", "unexpected token `;'"),
        ("cat <", "unexpected end of line"),
        ("cat >", "unexpected end of line"),
        ("cat <<<", "unexpected end of line"),
        ("cat > | cat", "unexpected token `|'"),
        ("a && && b", "unexpected token `&&'"),
        ("&", "unexpected token `&'"),
    ])
    def test_bad_line(self, line, message):
        with pytest.raises(ShellSyntaxError) as info:
            tree(line)
        assert message in str(info.value)

    def test_message_format(self):
        with pytest.raises(ShellSyntaxError) as info:
            tree("echo a &&")
        assert str(info.value) == "syntax error: unexpected end of line"

    @pytest.mark.parametrize("line", ["echo a ;", "echo a &", "a ; b ;"])
    def test_trailing_separator_is_fine(self, line):
        assert tree(line)
