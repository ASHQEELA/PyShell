import pytest

from conftest import unix_only
from myshell.errors import ShellSyntaxError
from myshell.heredoc import read_heredoc_body
from myshell.lexer import lex
from myshell.parser import parse


def source(*lines):
    it = iter(lines)
    return lambda: next(it, None)


class TestReadBody:
    def test_stops_at_the_delimiter(self):
        assert read_heredoc_body("EOF", False, source("a", "b", "EOF", "c")) == "a\nb\n"

    def test_delimiter_must_match_the_whole_line(self):
        body = read_heredoc_body("EOF", False, source("EOF x", " EOF", "EOF"))
        assert body == "EOF x\n EOF\n"

    def test_strip_tabs_removes_leading_tabs_only(self):
        body = read_heredoc_body("EOF", True, source("\t\ta", "\tb c", "\tEOF"))
        assert body == "a\nb c\n"

    def test_tabs_stay_without_the_dash(self):
        assert read_heredoc_body("EOF", False, source("\ta", "EOF")) == "\ta\n"

    def test_empty_body(self):
        assert read_heredoc_body("EOF", False, source("EOF")) == ""

    def test_end_of_input_warns_and_keeps_the_text(self, capsys):
        assert read_heredoc_body("EOF", False, source("a")) == "a\n"
        assert "wanted `EOF'" in capsys.readouterr().err

    def test_no_reader_gives_an_empty_body(self, capsys):
        assert read_heredoc_body("EOF", False, None) == ""
        assert "delimited by end-of-file" in capsys.readouterr().err


class TestLexAndParse:
    def test_operators(self):
        kinds = [(t.kind, t.text) for t in lex("cat <<EOF <<-X <<<s")]
        assert [text for _, text in kinds] == ["cat", "<<", "EOF", "<<-", "X", "<<<", "s"]

    def test_no_space_before_the_delimiter(self):
        assert [t.text for t in lex("cat<<EOF")] == ["cat", "<<", "EOF"]

    def test_parser_stores_the_delimiter(self):
        red = parse(lex("cat <<EOF"))[0].pipeline.commands[0].redirects[0]
        assert red.op == "<<" and red.target.text == "EOF" and red.body == ""

    def test_quoted_delimiter_is_marked(self):
        red = parse(lex("cat <<'EOF'"))[0].pipeline.commands[0].redirects[0]
        assert red.target.quoted and red.target.text == "EOF"

    def test_missing_delimiter_is_a_syntax_error(self):
        with pytest.raises(ShellSyntaxError):
            parse(lex("cat <<"))


@unix_only
class TestRun:
    def test_plain_body(self, sh):
        assert sh("cat <<EOF\nhello\nworld\nEOF").out == "hello\nworld\n"

    def test_variables_commands_and_math_expand(self, sh):
        r = sh("x=W\ncat <<EOF\nhi $x $(echo c) `echo b` $((1+2))\nEOF")
        assert r.out == "hi W c b 3\n"

    def test_backslash_escapes(self, sh):
        r = sh("x=1\ncat <<EOF\n\\$x \\\\ \\`z\\` a\\\nb\nEOF")
        assert r.out == "$x \\ `z` ab\n"

    def test_quotes_stay_literal_in_the_body(self, sh):
        r = sh("cat <<EOF\n\"q\" 'r'\nEOF")
        assert r.out == "\"q\" 'r'\n"

    def test_quoted_delimiter_blocks_expansion(self, sh):
        r = sh("x=1\ncat <<'EOF'\n$x `echo b` $((1+2)) \\$\nEOF")
        assert r.out == "$x `echo b` $((1+2)) \\$\n"

    def test_double_quoted_delimiter_blocks_expansion(self, sh):
        assert sh('x=1\ncat <<"EOF"\n$x\nEOF').out == "$x\n"

    def test_dash_strips_tabs(self, sh):
        assert sh("cat <<-EOF\n\tone\n\t\ttwo\n\tEOF").out == "one\ntwo\n"

    def test_command_after_the_heredoc_runs(self, sh):
        assert sh("cat <<EOF\na\nEOF\necho after").out == "a\nafter\n"

    def test_in_a_pipeline(self, sh):
        assert sh("cat <<EOF | tr a-z A-Z\nabc\nEOF").out == "ABC\n"

    def test_with_an_output_redirect(self, sh):
        sh("cat <<EOF > out.txt\nsaved\nEOF")
        assert (sh.work / "out.txt").read_text() == "saved\n"

    def test_two_heredocs_in_order(self, sh):
        r = sh("cat <<A; cat <<B\none\nA\ntwo\nB")
        assert r.out == "one\ntwo\n"

    def test_last_heredoc_wins_on_one_command(self, sh):
        assert sh("cat <<A <<B\none\nA\ntwo\nB").out == "two\n"

    def test_heredoc_to_a_builtin_in_the_shell(self, sh):
        assert sh("echo hi <<EOF\nx\nEOF").out == "hi\n"

    def test_large_body_does_not_block(self, sh):
        body = "\n".join(["line"] * 20000)
        assert sh(f"wc -l <<EOF\n{body}\nEOF").out.strip() == "20000"

    def test_missing_delimiter_warns(self, sh):
        r = sh("cat <<EOF\nonly")
        assert r.out == "only\n"
        assert "wanted `EOF'" in r.err

    def test_error_reports_the_first_line_number(self, sh):
        r = sh("echo ok\ncat <<EOF; echo 'oops\nbody\nEOF")
        assert "line 2" in r.err

    def test_blank_lines_in_the_body_stay(self, sh):
        assert sh("cat <<EOF\na\n\nb\nEOF").out == "a\n\nb\n"

    def test_script_file(self, run_cli, dirs):
        _, work = dirs
        (work / "s.sh").write_text("cat <<EOF\nfrom file\nEOF\necho end\n")
        assert run_cli(["s.sh"]).out == "from file\nend\n"

    def test_portable_path(self, run_cli):
        r = run_cli(["-c", "cat <<EOF\nport\nEOF"], portable=True)
        assert r.out == "port\n"

    def test_source_reads_the_body_from_the_file(self, sh):
        (sh.work / "lib.sh").write_text("cat <<EOF\nsourced\nEOF\n")
        assert sh("source lib.sh\necho next").out == "sourced\nnext\n"
