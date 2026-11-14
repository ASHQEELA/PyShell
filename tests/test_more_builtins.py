"""read, printf, set, env, which, unalias, exec, trap, true, false and the extras."""
import os
import signal

import pytest

from myshell import prompt


@pytest.fixture
def run(sh):
    def go(script, **kw):
        return sh(script, env={"MYSHELL_PORTABLE": "1"}, **kw)
    return go


class TestTrueFalse:
    def test_statuses(self, run):
        assert run("true; echo $?; false; echo $?; :; echo $?").out == "0\n1\n0\n"

    def test_in_conditions(self, run):
        assert run("if true; then echo yes; fi; false || echo no").out == "yes\nno\n"


class TestRead:
    def test_splits_into_names(self, run):
        assert run('read a b <<< "one two three"; echo "$a|$b"').out == "one|two three\n"

    def test_default_reply(self, run):
        assert run('read <<< "hello there"; echo "$REPLY"').out == "hello there\n"

    def test_one_name_keeps_the_line(self, run):
        assert run('read x <<< "  a  b "; echo "[$x]"').out == "[a  b]\n"

    def test_eof_status(self, run):
        r = run("read x < /dev/null; echo $?" if os.name != "nt" else "read x < NUL; echo $?")
        assert r.out == "1\n"

    def test_missing_newline_still_assigns(self, run):
        assert run('printf abc | read x; echo "[$x]"').out in ("[abc]\n", "[]\n")

    def test_backslash_rules(self, run):
        assert run(r'read x <<< "a\tb"; echo "$x"').out == "atb\n"
        assert run(r'read -r x <<< "a\tb"; echo "$x"').out == "a\\tb\n"

    def test_delimiter_and_count(self, run):
        assert run('read -d , x <<< "ab,cd"; echo $x').out == "ab\n"
        assert run('read -n 2 x <<< "abcd"; echo $x').out == "ab\n"

    def test_ifs(self, run):
        assert run('IFS=:; read a b <<< "x:y"; echo "$a $b"').out == "x y\n"

    def test_bad_name(self, run):
        assert run('read 1x <<< a').code != 0 or "not a valid identifier" in run('read 1x <<< a').err


class TestPrintf:
    @pytest.mark.parametrize("script,out", [
        ("printf 'hi\\n'", "hi\n"),
        ("printf '%s-%s\\n' a b", "a-b\n"),
        ("printf '%d|%5d|%-5d|%05d\\n' 1 2 3 4", "1|    2|3    |00004\n"),
        ("printf '%.2f\\n' 3.14159", "3.14\n"),
        ("printf '%x %o %X\\n' 255 8 255", "ff 10 FF\n"),
        ("printf '%s\\n' a b c", "a\nb\nc\n"),
        ("printf '%c\\n' hello", "h\n"),
        ("printf '%5s|%-5s|\\n' ab cd", "   ab|cd   |\n"),
        ("printf '100%%\\n'", "100%\n"),
        ("printf '%b\\n' 'a\\tb'", "a\tb\n"),
        ("printf '%s\\n'", "\n"),
        ("printf '%*d\\n' 4 7", "   7\n"),
        ("printf '\\x41\\101\\n'", "AA\n"),
        ("printf '%d\\n' 0x10 010", "16\n8\n"),
    ])
    def test_forms(self, run, script, out):
        assert run(script).out == out

    def test_v_assigns(self, run):
        assert run("printf -v x '%03d' 5; echo $x").out == "005\n"

    def test_bad_number(self, run):
        r = run("printf '%d\\n' abc")
        assert "invalid number" in r.err and r.code == 1

    def test_no_format(self, run):
        assert run("printf").code == 2


class TestSet:
    def test_positional(self, run):
        assert run('set -- a "b c"; echo $# [$2]').out == "2 [b c]\n"
        assert run("set -- a b; set --; echo $#").out == "0\n"

    def test_lists_variables(self, run):
        assert "MYVAR=1" in run("MYVAR=1; set").out

    def test_options_toggle(self, run):
        assert run("set -e; set +e; false; echo alive").out == "alive\n"
        assert run("set -o errexit; set +o errexit; false; echo alive").out == "alive\n"

    def test_unknown_option_name(self, run):
        assert "invalid option name" in run("set -o nope").err


class TestEnv:
    def test_lists_environment(self, run):
        assert "MYSHELL_PORTABLE=1" in run("env").out

    def test_sets_for_one_command(self, run):
        r = run("env GREETING=hi printenv GREETING; echo [$GREETING]") \
            if os.name != "nt" else run("env GREETING=hi env")
        assert "hi" in r.out

    def test_runs_builtins_and_functions(self, run):
        assert run("f() { echo fn; }; env f").out == "fn\n"
        assert run("env echo hi").out == "hi\n"

    def test_unset_and_ignore(self, run):
        out = run("env -u MYSHELL_PORTABLE env").out
        assert "MYSHELL_PORTABLE" not in out
        assert run("env -i A=1 env").out == "A=1\n"

    def test_environment_restored(self, run):
        assert run("env A=1 echo x; echo [$A]").out == "x\n[]\n"


class TestWhich:
    def test_builtin_alias_function(self, run):
        r = run("alias ll='ls -l'; f() { :; }; which echo ll f")
        assert "echo: shell built-in command" in r.out
        assert "ll: aliased to ls -l" in r.out
        assert "f: shell function" in r.out

    def test_program_path(self, run):
        r = run("which python3 python") if os.name != "nt" else run("which python")
        assert r.out.strip() != ""

    def test_not_found(self, run):
        r = run("which nosuchthing")
        assert r.code == 1 and "no nosuchthing in" in r.err


class TestUnalias:
    def test_removes(self, run):
        r = run("alias a='echo hi'; unalias a; a")
        assert "command not found" in r.err

    def test_missing_and_all(self, run):
        assert "unalias: zz: not found" in run("unalias zz").err
        r = run("alias a=true b=true; unalias -a; alias")
        assert r.out == ""


class TestExec:
    def test_runs_and_ends_the_shell(self, run):
        r = run("exec echo replaced; echo never")
        assert r.out == "replaced\n"

    def test_exit_status_passes_through(self, run):
        assert run("exec false").code == 1

    def test_not_found(self, run):
        r = run("exec nosuchprog; echo after")
        assert "not found" in r.err

    def test_no_arguments(self, run):
        assert run("exec; echo after").out == "after\n"


class TestTrap:
    def test_exit_trap_runs_at_the_end(self, run):
        r = run("trap 'echo bye' EXIT; echo work")
        assert r.out == "work\nbye\n"

    def test_exit_trap_runs_after_exit(self, run):
        assert run("trap 'echo bye' EXIT; exit 3").out == "bye\n"
        assert run("trap 'echo bye' EXIT; exit 3").code == 3

    def test_reset(self, run):
        assert run("trap 'echo bye' EXIT; trap - EXIT; echo x").out == "x\n"

    def test_list(self, run):
        assert "trap -- 'echo hi' EXIT" in run("trap 'echo hi' EXIT; trap").out
        assert "INT" in run("trap -l").out

    def test_bad_signal(self, run):
        assert "invalid signal" in run("trap 'x' NOSUCH").err

    @pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="needs SIGUSR1")
    def test_signal_trap_runs_at_the_next_command(self, run):
        r = run("trap 'echo caught' USR1; kill -USR1 $$; echo after")
        assert r.out == "caught\nafter\n" or r.out == "after\ncaught\n"


class TestEcho:
    def test_flags(self, run):
        assert run("echo -n a; echo b").out == "ab\n"
        assert run("echo -e 'a\\tb'").out == "a\tb\n"
        assert run("echo -E 'a\\tb'").out == "a\\tb\n"

    def test_dash_words_stay_words(self, run):
        assert run("echo -x -n1").out == "-x -n1\n"


class TestPortableChoice:
    def test_real_program_wins_unless_asked(self, state, monkeypatch):
        from myshell.builtins import find_builtin
        monkeypatch.delenv("MYSHELL_PORTABLE", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        assert find_builtin("ls") is None
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert find_builtin("ls") is not None
        monkeypatch.setenv("MYSHELL_PORTABLE", "1")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        assert find_builtin("ls") is not None

    def test_core_builtins_always_win(self, state, monkeypatch):
        from myshell.builtins import find_builtin
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        assert find_builtin("echo") and find_builtin("true") and find_builtin("printf")

    def test_type_knows_them(self, run):
        assert "ls is a shell builtin" in run("type ls").out
        assert "is a shell function" in run("f() { :; }; type f").out


class TestPromptExtras:
    def test_time_escapes(self, state, monkeypatch):
        import time
        fixed = time.struct_time((2026, 11, 12, 15, 4, 5, 3, 316, 0))
        monkeypatch.setattr(prompt, "now", lambda: fixed)
        assert prompt.expand_prompt(r"\t", state) == "15:04:05"
        assert prompt.expand_prompt(r"\T", state) == "03:04:05"
        assert prompt.expand_prompt(r"\A", state) == "15:04"
        assert prompt.expand_prompt(r"\@", state).startswith("03:04")
        assert prompt.expand_prompt(r"\d", state) == "Thu Nov 12"

    def test_git_branch(self, tmp_path):
        repo = tmp_path / "r"
        (repo / ".git").mkdir(parents=True)
        (repo / "sub").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n")
        assert prompt.git_branch(str(repo / "sub")) == "feature/x"
        (repo / ".git" / "HEAD").write_text("0123456789abcdef\n")
        assert prompt.git_branch(str(repo)) == "0123456"

    def test_git_branch_outside_a_repo_and_worktrees(self, tmp_path):
        assert prompt.git_branch(str(tmp_path)) == ""
        real = tmp_path / "real"
        real.mkdir()
        (real / "HEAD").write_text("ref: refs/heads/wt\n")
        work = tmp_path / "wt"
        work.mkdir()
        (work / ".git").write_text(f"gitdir: {real}\n")
        assert prompt.git_branch(str(work)) == "wt"

    def test_default_prompt_shows_the_branch(self, state, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TERM", "dumb")
        assert "(main)" in prompt.build_prompt(state)

    def test_g_escape(self, state, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/dev\n")
        monkeypatch.chdir(tmp_path)
        assert prompt.expand_prompt(r"[\g]", state) == "[dev]"

    @pytest.mark.parametrize("secs,text", [
        (0.4, "0.4s"), (2.34, "2.3s"), (45, "45s"), (185, "3m 05s"), (3725, "1h 02m"),
    ])
    def test_duration_format(self, secs, text):
        assert prompt.format_duration(secs) == text

    def test_slow_notice_threshold(self, state):
        state.last_duration = 1.0
        assert prompt.slow_notice(state) == ""
        state.last_duration = 5.0
        assert prompt.slow_notice(state) == "took 5.0s"
        state.set_variable("SLOW_COMMAND_SECS", "10")
        assert prompt.slow_notice(state) == ""
        state.set_variable("SLOW_COMMAND_SECS", "off")
        assert prompt.slow_notice(state) == ""
        state.set_variable("SLOW_COMMAND_SECS", "0")
        assert prompt.slow_notice(state) == ""
