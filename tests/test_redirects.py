import pytest

from conftest import unix_only

pytestmark = unix_only


@pytest.fixture(params=["fork", "portable"])
def both(request, sh):
    def run(script, **kw):
        return sh(script, portable=(request.param == "portable"), **kw)
    run.work = sh.work
    run.real_work = sh.real_work
    return run


NO_FILE = "ls: cannot access '/nonexist': No such file or directory"
ERR_TEXT = "No such file or directory"


class TestOutput:
    def test_truncate(self, both):
        assert both("echo a > f\necho b > f\ncat f").out == "b\n"

    def test_append(self, both):
        assert both("echo a > f\necho b >> f\ncat f").out == "a\nb\n"

    def test_stderr_to_a_file(self, both):
        r = both("ls /nonexist 2> e\ncat e")
        assert r.err == ""
        assert ERR_TEXT in r.out

    def test_stderr_append(self, both):
        r = both("echo one > e\nls /nonexist 2>> e\ncat e")
        assert r.out.splitlines()[0] == "one"
        assert ERR_TEXT in r.out

    def test_both_streams_to_one_file(self, both):
        r = both("ls /nonexist &> f\ncat f")
        assert r.err == ""
        assert ERR_TEXT in r.out

    def test_lone_redirect_creates_the_file(self, both):
        assert both("> made.txt\nls made.txt").out == "made.txt\n"

    def test_last_target_wins_and_all_are_created(self, both):
        r = both("echo x > a > b\ncat a\ncat b")
        assert r.out == "x\n"

    def test_redirect_before_the_command(self, both):
        assert both("> f echo hi\ncat f").out == "hi\n"

    def test_dev_null(self, both):
        r = both("ls /nonexist 2>/dev/null\necho $?")
        assert r.out == "2\n"
        assert r.err == ""


class TestCopyingStreams:
    def test_stderr_into_a_pipe(self, both):
        r = both("ls /nonexist 2>&1 | wc -l")
        assert r.out.strip() == "1"
        assert r.err == ""

    def test_without_the_copy_the_error_stays_on_stderr(self, both):
        r = both("ls /nonexist | wc -l")
        assert r.out.strip() == "0"
        assert ERR_TEXT in r.err

    def test_file_then_copy_sends_both_to_the_file(self, both):
        r = both("ls /nonexist > f 2>&1\ncat f")
        assert r.err == ""
        assert ERR_TEXT in r.out

    def test_copy_then_file_sends_only_stdout_to_the_file(self, both):
        r = both("ls /nonexist 2>&1 > f\ncat f")
        assert ERR_TEXT in r.out  # the error went to the original stdout
        assert r.err == ""
        assert both("ls /nonexist 2>&1 > f\nwc -c < f").out.strip().splitlines()[-1].strip() == "0"

    @pytest.mark.parametrize("op", ["1>&2", ">&2"])
    def test_stdout_to_stderr(self, both, op):
        r = both(f"echo x {op}")
        assert r.out == ""
        assert r.err == "x\n"

    def test_stdout_copy_onto_itself_changes_nothing(self, sh):
        assert sh("echo x >&1").out == "x\n"

    def test_dup_on_a_builtin(self, sh):
        r = sh("pwd extra 2>&1 > f\ncat f")
        assert r.out == "pwd: too many arguments\n"   # error to the original stdout, f empty

    def test_command_not_found_through_a_pipe(self, sh):
        assert sh("nonexistcmd 2>&1 | cat").out == "nonexistcmd: command not found\n"


class TestInput:
    @pytest.fixture(autouse=True)
    def input_file(self, sh):
        sh.work.joinpath("in.txt").write_text("one\ntwo\nthree\n")

    def test_read_a_file(self, both):
        assert both("cat < in.txt").out == "one\ntwo\nthree\n"

    def test_redirect_before_the_command(self, both):
        assert both("< in.txt cat").out == "one\ntwo\nthree\n"

    def test_count_lines(self, both):
        assert both("wc -l < in.txt").out.strip() == "3"

    def test_into_a_pipeline(self, both):
        assert both("cat < in.txt | tr a-z A-Z").out == "ONE\nTWO\nTHREE\n"

    def test_overrides_the_pipe(self, both):
        assert both("echo piped | cat < in.txt").out == "one\ntwo\nthree\n"

    def test_input_and_output_together(self, both):
        assert both("tr a-z A-Z < in.txt > up.txt\ncat up.txt").out == "ONE\nTWO\nTHREE\n"

    def test_missing_input_file(self, both):
        r = both("cat < /nonexist\necho $?")
        assert "/nonexist: No such file or directory" in r.err
        assert r.out == "1\n"

    def test_directory_as_input(self, both):
        r = both("cat < .\necho $?")
        assert "Is a directory" in r.err
        assert r.out == "1\n"


class TestHereString:
    def test_text(self, both):
        assert both("cat <<< hello").out == "hello\n"

    def test_quotes_keep_spacing(self, both):
        assert both("cat <<< 'a   b'").out == "a   b\n"

    def test_variables_expand(self, sh):
        assert sh('X=1\ncat <<< "v=$X"').out == "v=1\n"

    def test_single_quotes_block_expansion(self, sh):
        assert sh("X=1\ncat <<< '$X'").out == "$X\n"

    def test_command_substitution(self, sh):
        assert sh("cat <<< $(echo from-sub)").out == "from-sub\n"

    def test_unset_variable_gives_an_empty_line(self, sh):
        assert sh("cat <<< $UNSET").out == "\n"

    def test_feeds_a_pipeline(self, both):
        assert both("tr a-z A-Z <<< 'up me'").out == "UP ME\n"

    def test_long_text_does_not_block(self, sh):
        assert sh('wc -c <<< "$(seq 1 20000)"').out.strip() == "108894"


class TestEveryStage:
    def test_redirect_on_the_first_stage(self, both):
        assert both("echo hi > f | cat\ncat f").out == "hi\n"

    def test_stderr_of_a_middle_stage(self, both):
        r = both("ls /nonexist 2> e | cat\ncat e")
        assert ERR_TEXT in r.out

    def test_each_stage_has_its_own_redirects(self, both):
        both.work.joinpath("in.txt").write_text("abc\n")
        assert both("cat < in.txt | tr a-z A-Z > out.txt\ncat out.txt").out == "ABC\n"


class TestFailures:
    def test_bad_target_directory(self, both):
        r = both("echo x > /nonexistdir/f\necho $?")
        assert "/nonexistdir/f: No such file or directory" in r.err
        assert r.out == "1\n"

    def test_a_bad_redirect_starts_nothing(self, both):
        r = both("echo hi | cat > /nonexistdir/f\necho $?")
        assert r.out == "1\n"

    def test_ambiguous_target(self, both):
        both.work.joinpath("a1").write_text("")
        both.work.joinpath("a2").write_text("")
        r = both("echo x > a*\necho $?")
        assert "ambiguous redirect" in r.err
        assert r.out == "2\n"

    @pytest.mark.parametrize("script", ["cat <", "cat >", "cat <<<", "echo a > | cat"])
    def test_missing_target_is_a_syntax_error(self, sh, script):
        r = sh(script)
        assert "syntax error" in r.err
        assert r.code == 2
