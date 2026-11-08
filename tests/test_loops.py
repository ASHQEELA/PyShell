import pytest

from conftest import unix_only
from myshell.errors import ShellSyntaxError
from myshell.lexer import lex
from myshell.parser import ForClause, WhileClause, compound_open, iter_commands, parse


class TestParse:
    def test_while_shape(self):
        node = parse(lex("while a; do b; done"))[0].pipeline
        assert isinstance(node, WhileClause) and not node.until

    def test_until_shape(self):
        assert parse(lex("until a; do b; done"))[0].pipeline.until

    def test_for_shape(self):
        node = parse(lex("for x in 1 $y 'z w'; do b; done"))[0].pipeline
        assert isinstance(node, ForClause) and node.name == "x"
        assert [t.text for t in node.words] == ["1", "$y", "z w"]

    def test_for_without_in_has_no_words(self):
        assert parse(lex("for x; do b; done"))[0].pipeline.words == []

    def test_commands_inside_are_visible(self):
        names = [c.argv[0].text for c in iter_commands(parse(lex("while a; do b; done; for x in 1; do c; done")))]
        assert names == ["a", "b", "c"]

    @pytest.mark.parametrize("line", [
        "while a; do b", "for x in 1 2; do b", "while a", "for x in 1; do if a; then b; fi",
        "while a; do while b; do c; done",
    ])
    def test_open(self, line):
        assert compound_open(lex(line))

    @pytest.mark.parametrize("line", [
        "while a; do b; done", "for x in 1; do b; done; echo", "until a; do b; done",
        "while a; do if b; then c; fi; done",
    ])
    def test_closed(self, line):
        assert not compound_open(lex(line))

    @pytest.mark.parametrize("line", [
        "while a; done", "while a; do done", "for; do b; done", "for 1x in a; do b; done",
        "for 'x' in a; do b; done", "while a; do b; done | cat", "for x in a; do b; done &",
        "until; do b; done", "for x in a; b; done",
    ])
    def test_syntax_errors(self, line):
        with pytest.raises(ShellSyntaxError):
            parse(lex(line))


@unix_only
class TestRun:
    def test_while_counts(self, sh):
        r = sh("i=0\nwhile [ $i -lt 3 ]; do echo $i; i=$((i+1)); done")
        assert r.out == "0\n1\n2\n"

    def test_until_counts(self, sh):
        assert sh("i=0\nuntil [ $i -ge 2 ]; do echo $i; i=$((i+1)); done").out == "0\n1\n"

    def test_while_that_never_runs(self, sh):
        assert sh("while false; do echo x; done; echo $?").out == "0\n"

    def test_for_over_words(self, sh):
        assert sh("for x in a b c; do echo $x; done").out == "a\nb\nc\n"

    def test_for_expands_variables_commands_and_globs(self, sh):
        (sh.work / "a.txt").write_text("")
        (sh.work / "b.txt").write_text("")
        r = sh("L='p q'\nfor x in $L $(echo r) *.txt; do echo $x; done")
        assert r.out == "p\nq\nr\na.txt\nb.txt\n"

    def test_quoted_item_stays_one_word(self, sh):
        assert sh("for x in 'a b' c; do echo [$x]; done").out == "[a b]\n[c]\n"

    def test_variable_keeps_its_last_value(self, sh):
        assert sh("for x in 1 2 3; do true; done; echo $x").out == "3\n"

    def test_empty_list_runs_nothing(self, sh):
        assert sh("for x in; do echo no; done; echo $?").out == "0\n"

    def test_multiline(self, sh):
        assert sh("for x in 1 2\ndo\n  echo n$x\ndone\necho end").out == "n1\nn2\nend\n"

    def test_break(self, sh):
        assert sh("for x in 1 2 3; do [ $x = 2 ] && break; echo $x; done").out == "1\n"

    def test_continue(self, sh):
        assert sh("for x in 1 2 3; do [ $x = 2 ] && continue; echo $x; done").out == "1\n3\n"

    def test_break_from_inside_if(self, sh):
        assert sh("while true; do if true; then break; fi; done; echo out").out == "out\n"

    def test_break_two_levels(self, sh):
        r = sh("for a in 1 2; do for b in x y; do [ $b = y ] && break 2; echo $a$b; done; done; echo end")
        assert r.out == "1x\nend\n"

    def test_continue_two_levels(self, sh):
        r = sh("for a in 1 2; do for b in x y; do [ $b = y ] && continue 2; echo $a$b; done; done")
        assert r.out == "1x\n2x\n"

    def test_continue_three_levels_through_nested_loops(self, sh):
        r = sh("for a in 1 2; do for b in x; do for c in p q; do continue 3; echo no; done; done; echo no2; done; echo end")
        assert r.out == "end\n"

    def test_loop_depth_is_back_to_zero_after_a_while(self, sh):
        r = sh("while false; do true; done; break")
        assert "only meaningful" in r.err

    def test_count_larger_than_depth_is_clamped(self, sh):
        assert sh("for a in 1 2; do break 9; echo no; done; echo end").out == "end\n"

    def test_break_outside_a_loop_warns(self, sh):
        r = sh("break; echo after")
        assert r.out == "after\n" and "only meaningful in a `for'" in r.err

    @pytest.mark.parametrize("arg", ["0", "x", "-1"])
    def test_bad_count(self, sh, arg):
        r = sh(f"for a in 1; do break {arg}; done")
        assert "loop count out of range" in r.err

    def test_loop_depth_resets_after_break(self, sh):
        r = sh("for a in 1; do break; done; break")
        assert "only meaningful" in r.err

    def test_status_is_that_of_the_last_body_command(self, sh):
        assert sh("for x in 1 2; do false; done; echo $?").out == "1\n"

    def test_loop_in_a_pipeline_position_is_rejected(self, sh):
        assert "syntax error" in sh("for x in 1; do echo $x; done | cat").err

    def test_loop_inside_command_substitution(self, sh):
        assert sh('echo "$(for x in 1 2; do echo $x; done)"').out == "1\n2\n"

    def test_read_all_lines_with_a_pipe_inside(self, sh):
        assert sh("for x in a b; do echo $x | tr a-z A-Z; done").out == "A\nB\n"

    def test_heredoc_body_is_reused_each_pass(self, sh):
        r = sh("for x in 1 2; do cat <<EOF; done\nEOF")
        assert r.out == ""

    def test_missing_done_reports_the_start_line(self, sh):
        r = sh("echo ok\nwhile true; do\necho x")
        assert "line 2" in r.err and "unexpected end of file" in r.err

    def test_portable_path(self, run_cli):
        assert run_cli(["-c", "for x in 1 2; do echo $x; done"], portable=True).out == "1\n2\n"

    def test_script_file(self, run_cli, dirs):
        _, work = dirs
        (work / "s.sh").write_text("n=0\nwhile [ $n -lt 2 ]\ndo\n  echo $n\n  n=$((n+1))\ndone\n")
        assert run_cli(["s.sh"]).out == "0\n1\n"
