import os

import pytest

from conftest import unix_only
from myshell.builtins import evaluate_test
from myshell.errors import ShellError, ShellSyntaxError
from myshell.lexer import lex
from myshell.parser import IfClause, compound_open, iter_commands, parse


class TestTestBuiltin:
    @pytest.mark.parametrize("args, expected", [
        ([], False), (["a"], True), ([""], False),
        (["-z", ""], True), (["-z", "a"], False), (["-n", "a"], True), (["-n", ""], False),
        (["a", "=", "a"], True), (["a", "==", "b"], False), (["a", "!=", "b"], True),
        (["3", "-eq", "3"], True), (["3", "-ne", "3"], False), (["2", "-lt", "3"], True),
        (["3", "-le", "2"], False), (["3", "-gt", "2"], True), (["2", "-ge", "3"], False),
        (["-5", "-lt", "0"], True), (["3", "-lt", "3"], False), (["3", "-gt", "3"], False),
        (["!", "a"], False), (["!", ""], True), (["!", "a", "=", "b"], True),
        (["!", "!", "a"], True),
    ])
    def test_values(self, args, expected):
        assert evaluate_test(args) is expected

    def test_files(self, tmp_path):
        f = tmp_path / "f"
        f.write_text("x")
        empty = tmp_path / "e"
        empty.write_text("")
        d = str(tmp_path)
        assert evaluate_test(["-e", str(f)]) and evaluate_test(["-f", str(f)])
        assert not evaluate_test(["-f", d]) and evaluate_test(["-d", d])
        assert evaluate_test(["-s", str(f)]) and not evaluate_test(["-s", str(empty)])
        assert not evaluate_test(["-e", str(tmp_path / "none")])
        assert evaluate_test(["-r", str(f)]) and evaluate_test(["-w", str(f)])
        if os.name != "nt":  # Windows treats every file as executable
            assert not evaluate_test(["-x", str(f)])

    @pytest.mark.parametrize("args, message", [
        (["-q", "a"], "unary operator expected"),
        (["a", "~", "b"], "binary operator expected"),
        (["a", "-eq", "1"], "integer expression expected"),
        (["a", "b", "c", "d"], "too many arguments"),
    ])
    def test_errors(self, args, message):
        with pytest.raises(ShellError, match=message):
            evaluate_test(args)


@unix_only
class TestBuiltinRun:
    def test_status_codes(self, sh):
        r = sh("test 1 -eq 1; echo $?; test 1 -eq 2; echo $?; test 1 -eq x; echo $?")
        assert r.out == "0\n1\n2\n"
        assert "test: integer expression expected" in r.err

    def test_bracket(self, sh):
        assert sh("[ a = a ]; echo $?; [ a = b ]; echo $?").out == "0\n1\n"

    def test_bracket_needs_closing(self, sh):
        r = sh("[ a = a; echo $?")
        assert r.out == "2\n" and "missing `]'" in r.err

    def test_quoted_variable_with_spaces(self, sh):
        assert sh('x="a b"; [ "$x" = "a b" ] && echo same').out == "same\n"

    def test_empty_variable_unquoted(self, sh):
        assert sh("[ -z $UNSET ] && echo empty").out == "empty\n"

    def test_file_checks(self, sh):
        (sh.work / "f.txt").write_text("hi")
        r = sh("[ -f f.txt ] && echo file; [ -d f.txt ] || echo notdir")
        assert r.out == "file\nnotdir\n"


class TestParse:
    def test_shape(self):
        node = parse(lex("if a; then b; elif c; then d; else e; fi"))[0].pipeline
        assert isinstance(node, IfClause)
        assert len(node.clauses) == 2 and node.else_body is not None

    def test_commands_inside_are_visible(self):
        items = parse(lex("if a; then b; else c; fi; d"))
        names = [c.argv[0].text for c in iter_commands(items)]
        assert names == ["a", "b", "c", "d"]

    def test_then_after_a_word_is_just_an_argument(self):
        items = parse(lex("echo then fi"))
        assert [t.text for t in items[0].pipeline.commands[0].argv] == ["echo", "then", "fi"]

    def test_quoted_reserved_words_are_plain(self):
        assert not compound_open(lex("echo if")) and not compound_open(lex("'if' x"))

    @pytest.mark.parametrize("line", [
        "if a; then b", "if a", "if a; then", "if", "if a; then b; elif c; then d",
        "if a; then if b; then c; fi",
    ])
    def test_open(self, line):
        assert compound_open(lex(line))

    @pytest.mark.parametrize("line", ["if a; then b; fi", "a; if b; then c; fi", "if a; then if b; then c; fi; fi",
                                  "if a; then b; fi if"])
    def test_closed(self, line):
        assert not compound_open(lex(line))

    @pytest.mark.parametrize("line", [
        "if a; fi", "if a; then fi", "if ; then b; fi", "if a; then b; fi | cat",
        "if a; then b; fi > x", "if a; then b; fi &", "if a; then b; else fi",
    ])
    def test_syntax_errors(self, line):
        with pytest.raises(ShellSyntaxError):
            parse(lex(line))


@unix_only
class TestRun:
    def test_true_branch(self, sh):
        assert sh("if true; then echo yes; else echo no; fi").out == "yes\n"

    def test_false_branch(self, sh):
        assert sh("if false; then echo yes; else echo no; fi").out == "no\n"

    def test_elif_chain(self, sh):
        script = "x=2\nif [ $x -eq 1 ]; then echo one\nelif [ $x -eq 2 ]; then echo two\nelse echo other\nfi"
        assert sh(script).out == "two\n"

    def test_multiline(self, sh):
        assert sh("if true\nthen\n  echo a\n  echo b\nfi\necho end").out == "a\nb\nend\n"

    def test_nested(self, sh):
        script = "if true; then\n if false; then echo x; else echo inner; fi\nfi"
        assert sh(script).out == "inner\n"

    def test_no_branch_runs_status_zero(self, sh):
        assert sh("if false; then echo x; fi; echo $?").out == "0\n"

    def test_condition_can_be_a_list(self, sh):
        assert sh("if false || true; then echo ok; fi").out == "ok\n"

    def test_condition_can_be_a_pipeline(self, sh):
        assert sh("if echo abc | grep -q b; then echo found; fi").out == "found\n"

    def test_chained_after_fi(self, sh):
        assert sh("if true; then echo a; fi && echo b").out == "a\nb\n"

    def test_body_status_is_the_result(self, sh):
        assert sh("if true; then false; fi; echo $?").out == "1\n"

    def test_assignments_in_the_body_stay(self, sh):
        assert sh("if true; then x=7; fi; echo $x").out == "7\n"

    def test_cd_in_the_body_stays(self, sh):
        (sh.work / "d").mkdir()
        assert sh("if true; then cd d; fi; pwd").out.strip().endswith("/d")

    def test_comments_between_lines(self, sh):
        assert sh("if true # c\nthen # c\n# full\necho a\nfi").out == "a\n"

    def test_missing_fi_is_an_error_with_the_start_line(self, sh):
        r = sh("echo ok\nif true; then\necho x")
        assert "line 2" in r.err and "unexpected end of file" in r.err

    def test_script_file(self, run_cli, dirs):
        _, work = dirs
        (work / "s.sh").write_text("if [ -d . ]; then\n  echo here\nfi\n")
        assert run_cli(["s.sh"]).out == "here\n"

    def test_portable_path(self, run_cli):
        assert run_cli(["-c", "if [ 1 -lt 2 ]; then echo p; fi"], portable=True).out == "p\n"
