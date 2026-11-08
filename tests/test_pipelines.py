import pytest

from conftest import unix_only

pytestmark = unix_only


@pytest.fixture(params=["fork", "portable"])
def both(request, sh):
    """Runs a script with fork and pipes, then again with the subprocess fallback."""
    def run(script, **kw):
        return sh(script, portable=(request.param == "portable"), **kw)
    run.work = sh.work
    run.home = sh.home
    run.real_work = sh.real_work
    return run


class TestPipes:
    def test_one_pipe(self, both):
        assert both("echo hello | tr a-z A-Z").out == "HELLO\n"

    def test_three_stages(self, both):
        assert both("printf 'b\\na\\nc\\n' | sort | head -2").out == "a\nb\n"

    def test_status_is_the_last_stage(self, both):
        assert both("false | true\necho $?").out == "0\n"
        assert both("true | false\necho $?").out == "1\n"

    def test_large_output_does_not_block(self, both):
        assert both("seq 1 100000 | wc -l").out.strip() == "100000"

    def test_reader_that_quits_early(self, both):
        r = both("yes | head -2")
        assert r.out == "y\ny\n"
        assert r.code == 0


class TestMissingCommands:
    def test_not_found(self, both):
        r = both("nonexist\necho $?")
        assert "nonexist: command not found" in r.err
        assert r.out == "127\n"

    def test_path_that_does_not_exist(self, both):
        r = both("./nope\necho $?")
        assert "./nope: No such file or directory" in r.err
        assert r.out == "127\n"

    def test_not_executable(self, both):
        both.work.joinpath("plain").write_text("data")
        r = both("./plain\necho $?")
        assert "Permission denied" in r.err
        assert r.out == "126\n"

    def test_missing_command_in_the_middle_does_not_stop_the_rest(self, both):
        r = both("echo hi | nonexist_cmd | cat\necho $?")
        assert "nonexist_cmd: command not found" in r.err
        assert r.out == "0\n"

    def test_missing_command_at_the_end(self, both):
        assert both("echo hi | nonexist_cmd\necho $?").out == "127\n"


class TestLists:
    @pytest.mark.parametrize("script, out", [
        ("true && echo a", "a\n"),
        ("false && echo a\necho b", "b\n"),
        ("false || echo a", "a\n"),
        ("true || echo a\necho b", "b\n"),
        ("echo a; echo b", "a\nb\n"),
        ("false && echo x || echo y", "y\n"),
        ("true && echo x || echo y", "x\n"),
        ("echo a ;", "a\n"),
    ])
    def test_operators(self, both, script, out):
        assert both(script).out == out


class TestQuoting:
    def test_operators_inside_quotes_are_text(self, both):
        r = both("echo \"a | b\" \"c && d\" 'e ; f'")
        assert r.out == "a | b c && d e ; f\n"

    def test_escaped_operators_are_text(self, both):
        assert both(r"echo a \| b \&\& c").out == "a | b && c\n"


class TestBuiltinsInPipelines:
    def test_builtin_writes_into_a_pipe(self, sh):
        assert sh("echo hi | cat").out == "hi\n"
        assert sh("pwd | cat").out == sh.real_work + "\n"

    def test_builtin_error_goes_through_a_dup(self, sh):
        assert sh("pwd extra 2>&1 | cat").out == "pwd: too many arguments\n"

    def test_alias_listing_in_a_pipe(self, sh):
        assert sh("alias a=1\nalias | cat").out == "alias a='1'\n"


class TestBackground:
    def test_background_prints_a_job_number_and_does_not_wait(self, both):
        r = both("sleep 0.3 &\necho fast")
        lines = r.out.splitlines()
        assert lines[0].startswith("[1] ")
        assert "fast" in lines

    def test_background_job_output_is_kept(self, sh):
        r = sh("echo bg > out.txt &\nwait\ncat out.txt")
        assert r.out.endswith("bg\n")
