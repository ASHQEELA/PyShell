import os

import pytest

from conftest import unix_only

pytestmark = unix_only


class TestEchoPwd:
    def test_echo_joins_arguments_with_one_space(self, sh):
        assert sh("echo a   b").out == "a b\n"

    def test_echo_without_arguments_prints_a_newline(self, sh):
        assert sh("echo").out == "\n"

    def test_pwd(self, sh):
        assert sh("pwd").out == sh.real_work + "\n"

    def test_pwd_with_an_argument_fails(self, sh):
        r = sh("pwd extra")
        assert r.out == ""
        assert "pwd: too many arguments" in r.err
        assert r.code == 1


class TestCd:
    def test_cd_into_a_directory(self, sh):
        assert sh("mkdir sub\ncd sub\npwd").out == f"{sh.real_work}/sub\n"

    def test_cd_without_arguments_goes_home(self, sh):
        assert sh("cd\npwd").out == os.path.realpath(sh.home) + "\n"

    def test_cd_dash_returns_and_prints_the_directory(self, sh):
        r = sh("mkdir a b\ncd a\ncd ../b\ncd -\npwd", env={"OLDPWD": ""})
        assert r.out.splitlines() == [f"{sh.real_work}/a", f"{sh.real_work}/a"]

    def test_cd_dash_without_a_previous_directory(self, sh):
        r = sh("cd -", env={"OLDPWD": ""})
        assert "OLDPWD not set" in r.err
        assert r.code == 1

    def test_cd_updates_pwd_and_oldpwd(self, sh):
        r = sh("mkdir d\ncd d\necho $OLDPWD\necho $PWD")
        assert r.out.splitlines() == [sh.real_work, f"{sh.real_work}/d"]

    def test_missing_directory(self, sh):
        r = sh("cd /nonexistent")
        assert "cd: /nonexistent: No such file or directory" in r.err
        assert r.code == 1

    def test_too_many_arguments(self, sh):
        r = sh("cd a b")
        assert "cd: too many arguments" in r.err
        assert r.code == 1

    def test_failed_cd_keeps_the_directory(self, sh):
        assert sh("cd /nonexistent\npwd").out == sh.real_work + "\n"

    def test_cd_in_a_pipeline_does_not_move_the_shell(self, sh):
        r = sh("mkdir sub\ncd sub | cat\npwd")
        assert r.out == sh.real_work + "\n"


class TestAlias:
    def test_define_and_use(self, sh):
        assert sh("alias hi='echo hello'\nhi").out == "hello\n"

    def test_arguments_follow_the_alias(self, sh):
        assert sh("alias e='echo pre'\ne post").out == "pre post\n"

    def test_list(self, sh):
        assert sh("alias a=1\nalias b=2\nalias").out == "alias a='1'\nalias b='2'\n"

    def test_show_one(self, sh):
        assert sh("alias a=1\nalias a").out == "alias a='1'\n"

    def test_unknown_alias(self, sh):
        r = sh("alias nope")
        assert "alias: nope: not found" in r.err
        assert r.code == 1

    def test_alias_with_a_pipe(self, sh):
        assert sh("alias up='tr a-z A-Z'\necho abc | up").out == "ABC\n"

    def test_alias_may_wrap_its_own_command(self, sh):
        assert sh("alias echo='echo wrapped'\necho x").out == "wrapped x\n"


class TestType:
    def test_builtin(self, sh):
        assert sh("type cd").out == "cd is a shell builtin\n"

    def test_alias(self, sh):
        assert sh("alias x=ls\ntype x").out == "x is an alias for 'ls'\n"

    def test_program(self, sh):
        assert sh("type ls").out.startswith("ls is /")

    def test_not_found(self, sh):
        r = sh("type nonexist_cmd")
        assert "nonexist_cmd: not found" in r.err
        assert r.code == 1

    def test_missing_argument(self, sh):
        r = sh("type")
        assert "type: missing argument" in r.err
        assert r.code == 1


class TestExit:
    @pytest.mark.parametrize("script, code", [
        ("exit 3", 3),
        ("exit", 0),
        ("false\nexit", 1),
        ("exit 256", 0),
        ("exit -1", 255),
    ])
    def test_exit_codes(self, sh, script, code):
        assert sh(script).code == code

    def test_nothing_runs_after_exit(self, sh):
        r = sh("echo a\nexit 4\necho b")
        assert r.out == "a\n"
        assert r.code == 4

    def test_non_numeric_argument(self, sh):
        r = sh("exit abc")
        assert "numeric argument required" in r.err
        assert r.code == 2

    def test_too_many_arguments_does_not_exit(self, sh):
        r = sh("exit 1 2\necho after")
        assert "too many arguments" in r.err
        assert r.out == "after\n"

    def test_exit_in_a_pipeline_only_ends_that_stage(self, sh):
        assert sh("exit 3 | cat\necho still").out == "still\n"


class TestVariables:
    def test_export_reaches_child_processes(self, sh):
        assert sh("export A=1\nenv | grep '^A='").out == "A=1\n"

    def test_plain_assignment_is_not_exported(self, sh):
        r = sh("B=2\necho $B\nenv | grep -c '^B='")
        assert r.out == "2\n0\n"

    def test_export_after_assignment(self, sh):
        assert sh("B=2\nexport B\nenv | grep '^B='").out == "B=2\n"

    def test_reassigning_an_exported_variable_keeps_it_exported(self, sh):
        assert sh("export V=1\nV=2\nenv | grep '^V='").out == "V=2\n"

    def test_unset(self, sh):
        r = sh('export C=3\nunset C\necho "[$C]"\nenv | grep -c "^C="')
        assert r.out == "[]\n0\n"

    def test_export_lists_variables(self, sh):
        assert 'declare -x TERM="dumb"' in sh("export").out

    @pytest.mark.parametrize("script", ["export 1bad=x", "unset 1x", "export a-b=1"])
    def test_invalid_names(self, sh, script):
        r = sh(script)
        assert "not a valid identifier" in r.err
        assert r.code == 1

    def test_assignment_before_a_program_is_temporary(self, sh):
        r = sh('FOO=x env | grep "^FOO="\necho "[$FOO]"')
        assert r.out == "FOO=x\n[]\n"

    def test_assignment_before_a_builtin_is_temporary(self, sh):
        assert sh('FOO=temp echo hi\necho "[$FOO]"').out == "hi\n[]\n"

    def test_several_assignments_before_a_program(self, sh):
        r = sh("FOO=1 BAR=2 env | grep -E '^(FOO|BAR)=' | sort")
        assert r.out == "BAR=2\nFOO=1\n"

    def test_assignment_alone_sets_a_shell_variable(self, sh):
        r = sh("FOO=x\necho $FOO\nenv | grep -c '^FOO='")
        assert r.out == "x\n0\n"


class TestSource:
    def test_source_and_dot(self, sh):
        sh.work.joinpath("s.rc").write_text("echo from-source\nexport SRC=1\n")
        assert sh("source s.rc\necho $SRC").out == "from-source\n1\n"
        assert sh(". s.rc\necho $SRC").out == "from-source\n1\n"

    def test_comments_and_blank_lines_are_ignored(self, sh):
        sh.work.joinpath("s.rc").write_text("# comment\n\n   \necho ok # trailing\n")
        assert sh("source s.rc").out == "ok\n"

    def test_missing_file(self, sh):
        r = sh("source nope.rc")
        assert "source: nope.rc: No such file or directory" in r.err
        assert r.code == 1

    def test_missing_argument(self, sh):
        r = sh("source")
        assert "filename argument required" in r.err
        assert r.code == 2

    def test_a_bad_line_is_reported_with_its_number_and_the_rest_runs(self, sh):
        sh.work.joinpath("s.rc").write_text('echo ok\necho "bad\necho after\n')
        r = sh("source s.rc")
        assert r.out == "ok\nafter\n"
        assert "s.rc: line 2: syntax error: unterminated quote" in r.err

    def test_exit_inside_a_sourced_file_ends_the_shell(self, sh):
        sh.work.joinpath("s.rc").write_text("exit 5\n")
        r = sh("source s.rc\necho never")
        assert r.code == 5
        assert r.out == ""


class TestStatus:
    @pytest.mark.parametrize("script, expected", [
        ("echo $?", "0"),
        ("false\necho $?", "1"),
        ("true\necho $?", "0"),
        ("nonexist\necho $?", "127"),
        ("false || echo $?", "1"),
    ])
    def test_dollar_question(self, sh, script, expected):
        assert sh(script).out.splitlines()[-1] == expected

    def test_syntax_error_sets_status_two(self, sh):
        assert sh('echo "bad\necho $?').out == "2\n"

    def test_last_status_is_the_exit_code(self, sh):
        assert sh("true\nfalse").code == 1
        assert sh("false\ntrue").code == 0


class TestJobBuiltins:
    def test_wait_blocks_until_background_jobs_finish(self, sh):
        r = sh("sleep 0.3 &\nwait\necho done")
        lines = r.out.splitlines()
        assert lines[0].startswith("[1] ")
        assert lines[-1] == "done"

    def test_wait_for_a_job_returns_its_status(self, sh):
        assert sh("false &\nwait %1\necho $?").out.splitlines()[-1] == "1"

    def test_wait_for_an_unknown_job(self, sh):
        r = sh("wait %9")
        assert "no such job" in r.err
        assert r.code == 127

    def test_jobs_lists_running_jobs(self, sh):
        r = sh("sleep 0.6 &\njobs\nwait")
        assert "[1]+  Running" in r.out
        assert "sleep 0.6 &" in r.out

    @pytest.mark.parametrize("name", ["fg", "bg"])
    def test_no_current_job(self, sh, name):
        r = sh(name)
        assert f"{name}: no current job" in r.err
        assert r.code == 1
