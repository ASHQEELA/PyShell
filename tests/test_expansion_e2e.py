import re

import pytest

from conftest import unix_only

pytestmark = unix_only


class TestVariables:
    def test_set_and_use(self, sh):
        assert sh("X=1\necho $X").out == "1\n"

    def test_unset_variable_disappears(self, sh):
        assert sh("echo a $UNSET b").out == "a b\n"

    def test_empty_arguments_survive(self, sh):
        assert sh("printf '[%s]\\n' \"\" '' x").out == "[]\n[]\n[x]\n"
        assert sh('printf "[%s]\\n" "$UNSET" $UNSET x').out == "[]\n[x]\n"

    def test_splitting(self, sh):
        assert sh('F="-l -a"\nprintf "[%s]\\n" $F').out == "[-l]\n[-a]\n"
        assert sh('F="-l -a"\nprintf "[%s]\\n" "$F"').out == "[-l -a]\n"

    def test_a_program_gets_the_split_words(self, sh):
        sh.work.joinpath("f.txt").write_text("")
        assert sh('F="-d -1"\nls $F f.txt').out == "f.txt\n"

    def test_process_id(self, sh):
        assert re.fullmatch(r"\d+\n", sh("echo $$").out)

    def test_escaped_dollar(self, sh):
        assert sh(r'echo \$HOME "\$HOME" ' + "'$HOME'").out == "$HOME $HOME $HOME\n"


class TestTildeAndGlobs:
    def test_tilde(self, sh):
        assert sh("echo ~ ~/x").out == f"{sh.home} {sh.home}/x\n"

    def test_quoted_tilde(self, sh):
        assert sh('echo "~"').out == "~\n"

    def test_glob(self, sh):
        for name in ("a.txt", "b.txt", "c.md"):
            sh.work.joinpath(name).write_text("")
        assert sh("echo *.txt").out == "a.txt b.txt\n"
        assert sh('echo "*.txt"').out == "*.txt\n"
        assert sh("echo *.nomatch").out == "*.nomatch\n"
        assert sh("P='*.txt'\necho $P").out == "a.txt b.txt\n"

    def test_glob_in_an_argument_of_a_program(self, sh):
        sh.work.joinpath("a.txt").write_text("1\n")
        sh.work.joinpath("b.txt").write_text("2\n")
        assert sh("cat *.txt").out == "1\n2\n"

    def test_names_with_spaces(self, sh):
        sh.work.joinpath("my file.txt").write_text("hi\n")
        assert sh("cat my\\ file.txt").out == "hi\n"
        assert sh('cat "my file.txt"').out == "hi\n"
        assert sh("cat my*").out == "hi\n"


class TestCommandSubstitution:
    def test_basic(self, sh):
        assert sh("echo $(echo a b)").out == "a b\n"

    def test_quoted_keeps_spacing_of_the_output(self, sh):
        assert sh('echo "[$(printf "a   b")]"').out == "[a   b]\n"

    def test_nested(self, sh):
        assert sh("echo $(echo $(echo deep))").out == "deep\n"

    def test_with_a_pipe_inside(self, sh):
        assert sh("echo $(echo abc | tr a-z A-Z)").out == "ABC\n"

    def test_operator_characters_in_the_output_stay_text(self, sh):
        assert sh('echo $(echo ";" "&&" "|")').out == "; && |\n"

    def test_joins_with_text_around_it(self, sh):
        assert sh("echo pre$(echo MID)post").out == "preMIDpost\n"

    def test_assignment_keeps_the_whole_output(self, sh):
        assert sh('x=$(echo hi there)\necho "[$x]"').out == "[hi there]\n"

    def test_changes_inside_do_not_reach_the_shell(self, sh):
        r = sh("mkdir sub\necho $(cd sub; pwd)\npwd")
        assert r.out.splitlines() == [f"{sh.real_work}/sub", sh.real_work]

    def test_variables_set_inside_are_local(self, sh):
        assert sh("V=1\necho $(V=2; echo $V) $V").out == "2 1\n"

    def test_aliases_work_inside(self, sh):
        assert sh("alias hi='echo aliased'\necho $(hi)").out == "aliased\n"

    def test_exit_inside_only_ends_the_substitution(self, sh):
        assert sh('echo "[$(exit 3)]"').out == "[]\n"

    def test_error_text_inside_goes_to_stderr(self, sh):
        r = sh('echo "[$(nonexist_cmd)]"')
        assert r.out == "[]\n"
        assert "nonexist_cmd: command not found" in r.err

    def test_stderr_can_be_captured(self, sh):
        assert "No such file" in sh('echo "[$(ls /nonexist 2>&1)]"').out

    def test_quoted_dollar_paren_is_text(self, sh):
        assert sh("echo '$(echo no)' \"\\$(echo no)\"").out == "$(echo no) $(echo no)\n"

    def test_unterminated(self, sh):
        r = sh("echo $(echo")
        assert "unterminated command substitution" in r.err
        assert r.code == 2

    def test_large_output(self, sh):
        assert sh('echo "$(seq 1 20000)" | wc -l').out.strip() == "20000"

    def test_not_available_without_fork(self, sh):
        r = sh("echo $(echo hi)", portable=True)
        assert "not supported on this platform" in r.err


class TestSyntaxErrors:
    @pytest.mark.parametrize("script", [
        "echo a &&", "echo a | | cat", 'echo "open', "; echo", "&& echo", "echo a |",
    ])
    def test_reported_with_status_two(self, sh, script):
        r = sh(script)
        assert "syntax error" in r.err
        assert r.code == 2

    def test_later_lines_still_run(self, sh):
        r = sh('echo "open\necho still-here')
        assert r.out == "still-here\n"

    def test_error_names_the_line(self, sh):
        assert "-c: line 3: syntax error" in sh("echo a\necho b\necho c &&").err


class TestComments:
    def test_trailing_comment(self, sh):
        assert sh("echo a # b | c").out == "a\n"

    def test_hash_inside_a_word(self, sh):
        assert sh("echo a#b").out == "a#b\n"

    def test_comment_line(self, sh):
        assert sh("# nothing\necho x").out == "x\n"
