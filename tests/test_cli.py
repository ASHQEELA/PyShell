import sys

from conftest import unix_only

pytestmark = unix_only


class TestCommandString:
    def test_runs_and_exits_with_the_last_status(self, run_cli):
        r = run_cli(["-c", "echo one; false"])
        assert r.out == "one\n"
        assert r.code == 1

    def test_exit_builtin_sets_the_code(self, run_cli):
        assert run_cli(["-c", "exit 7"]).code == 7

    def test_command_not_found_gives_127(self, run_cli):
        r = run_cli(["-c", "nonexist"])
        assert r.code == 127
        assert "command not found" in r.err

    def test_option_without_an_argument(self, run_cli):
        r = run_cli(["-c"])
        assert r.code == 2
        assert "option requires an argument" in r.err

    def test_no_prompt_is_printed(self, run_cli):
        assert run_cli(["-c", "echo hi"]).out == "hi\n"

    def test_multiple_lines(self, run_cli):
        assert run_cli(["-c", "echo a\necho b"]).out == "a\nb\n"

    def test_syntax_error_status(self, run_cli):
        r = run_cli(["-c", 'echo "x'])
        assert r.code == 2
        assert "-c: line 1: syntax error: unterminated quote" in r.err


class TestScriptFile:
    def test_runs_a_file(self, run_cli, dirs):
        _, work = dirs
        (work / "s.sh").write_text("echo from-script\nfalse\n")
        r = run_cli(["s.sh"])
        assert r.out == "from-script\n"
        assert r.code == 1

    def test_error_messages_use_the_file_name(self, run_cli, dirs):
        _, work = dirs
        (work / "s.sh").write_text("echo ok\necho 'bad\n")
        assert "s.sh: line 2: syntax error" in run_cli(["s.sh"]).err

    def test_missing_file(self, run_cli):
        r = run_cli(["nope.sh"])
        assert r.code == 127
        assert "nope.sh: No such file or directory" in r.err

    def test_rc_file_is_not_read_for_scripts(self, run_cli, dirs):
        home, work = dirs
        (home / ".myshellrc").write_text("echo from-rc\n")
        (work / "s.sh").write_text("echo body\n")
        assert run_cli(["s.sh"]).out == "body\n"


class TestPipedInput:
    def test_script_on_stdin_keeps_the_plain_prompt(self, dirs):
        import subprocess
        from conftest import clean_env
        home, work = dirs
        p = subprocess.run([sys.executable, "-m", "myshell"], input="echo hi\nexit 3\n",
                           cwd=work, env=clean_env(home), capture_output=True, text=True,
                           timeout=30)
        assert p.stdout == "$ hi\n$ "
        assert p.returncode == 3

    def test_end_of_input_exits_with_the_last_status(self, dirs):
        import subprocess
        from conftest import clean_env
        home, work = dirs
        p = subprocess.run([sys.executable, "-m", "myshell"], input="false\n",
                           cwd=work, env=clean_env(home), capture_output=True, text=True,
                           timeout=30)
        assert p.returncode == 1
        assert p.stdout.endswith("exit\n")


def test_launcher_script_works(dirs):
    import subprocess
    from conftest import ROOT, clean_env
    home, work = dirs
    p = subprocess.run([sys.executable, str(ROOT / "shell.py"), "-c", "echo launched"],
                       cwd=work, env=clean_env(home), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    assert p.stdout == "launched\n"
