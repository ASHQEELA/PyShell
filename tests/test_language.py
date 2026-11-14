"""Functions, arguments, case, brace and parameter expansion, run end to end.
Each script runs on the normal path and on the portable path (no fork)."""
import pytest


@pytest.fixture(params=[False, True], ids=["fork", "portable"])
def run(request, sh):
    def go(script, **kw):
        # MYSHELL_PORTABLE=1 makes ls, tr, head... the built-in versions on every system
        return sh(script, portable=request.param, env={"MYSHELL_PORTABLE": "1"}, **kw)
    go.sh = sh
    return go


class TestFunctions:
    def test_define_and_call(self, run):
        assert run("hi() { echo hello $1; }; hi Pip").out == "hello Pip\n"

    def test_arguments(self, run):
        r = run('f() { echo "$# [$1] [$2] [$@]"; }; f a "b c" d')
        assert r.out == "3 [a] [b c] [a b c d]\n"

    def test_quoted_at_keeps_words(self, run):
        r = run('f() { for x in "$@"; do echo "<$x>"; done; }; f "a b" c')
        assert r.out == "<a b>\n<c>\n"

    def test_quoted_at_with_no_arguments_is_nothing(self, run):
        assert run('f() { echo [ "$@" ]; }; f').out == "[ ]\n"

    def test_return_sets_status(self, run):
        assert run("f() { return 7; echo no; }; f; echo $?").out == "7\n"

    def test_return_without_number_uses_last_status(self, run):
        assert run("f() { false; return; }; f; echo $?").out == "1\n"

    def test_return_outside_function_is_an_error(self, run):
        r = run("return 1")
        assert "can only `return'" in r.err

    def test_multiline_definition(self, run):
        r = run("f() {\n  echo one\n  echo two\n}\nf")
        assert r.out == "one\ntwo\n"

    def test_function_keyword(self, run):
        assert run("function g { echo g; }; g").out == "g\n"

    def test_function_overrides_builtin(self, run):
        assert run("echo() { printf 'mine\\n'; }; echo x").out == "mine\n"

    def test_positional_params_are_restored(self, run):
        r = run("set -- outer; f() { echo $1; }; f inner; echo $1")
        assert r.out == "inner\nouter\n"

    def test_local_variables(self, run):
        r = run("v=out; f() { local v=in; echo $v; }; f; echo $v")
        assert r.out == "in\nout\n"

    def test_local_outside_function(self, run):
        assert "only be used in a function" in run("local x=1").err

    def test_recursion(self, run):
        script = ("fact() { if [ $1 -le 1 ]; then echo 1; "
                  "else echo $(( $1 * $(fact $(($1 - 1))) )); fi; }; fact 6")
        assert run(script).out == "720\n"

    def test_runaway_recursion_stops(self, run):
        r = run("f() { f; }; f; echo after")
        assert "nesting level" in r.err and "after" in r.out

    def test_function_in_pipeline(self, run):
        r = run("up() { tr a-z A-Z; }; echo hi | up")
        assert r.out == "HI\n"

    def test_function_output_redirect(self, run, dirs):
        _, work = dirs
        run("f() { echo saved; }; f > out.txt")
        assert (work / "out.txt").read_text() == "saved\n"

    def test_shift(self, run):
        assert run("f() { shift; echo $1 $#; }; f a b c").out == "b 2\n"

    def test_shift_too_far(self, run):
        assert "shift count out of range" in run("f() { shift 5; }; f a").err

    def test_dollar_zero(self, run):
        assert run("echo $0").out != ""

    def test_bad_function_name(self, run):
        assert run("1bad() { echo x; }").err != ""


class TestScriptArguments:
    def test_script_file_arguments(self, sh, dirs):
        _, work = dirs
        (work / "s.sh").write_text('echo "$0 $# $1 $2"\nfor a; do echo "[$a]"; done\n')
        r = sh_args(sh, ["s.sh", "x", "y z"])
        assert r.out == "s.sh 2 x y z\n[x]\n[y z]\n"

    def test_dash_c_arguments(self, run_cli):
        r = run_cli(["-c", 'echo "$0 $1 $#"', "name", "one", "two"])
        assert r.out == "name one 2\n"


def sh_args(sh, args):
    return sh.__wrapped_run_cli(args) if hasattr(sh, "__wrapped_run_cli") else _run(sh, args)


def _run(sh, args):
    import subprocess
    import sys
    from conftest import clean_env
    p = subprocess.run([sys.executable, "-m", "myshell", *args], cwd=sh.work,
                       env=clean_env(sh.home), stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=30)
    from conftest import Result
    return Result(p.stdout, p.stderr, p.returncode)


class TestBareFor:
    def test_loops_over_arguments(self, run):
        assert run("f() { for x; do echo $x; done; }; f a b").out == "a\nb\n"

    def test_in_with_nothing_runs_zero_times(self, run):
        assert run("for x in; do echo no; done; echo ok").out == "ok\n"


class TestParameterExpansion:
    @pytest.mark.parametrize("script,out", [
        ("echo ${X:-dflt}", "dflt"),
        ("X=v; echo ${X:-dflt}", "v"),
        ("X=; echo ${X:-dflt}", "dflt"),
        ("X=; echo [${X-dflt}]", "[]"),
        ("echo ${X:=set}; echo $X", "set\nset"),
        ("echo [${X:+alt}]", "[]"),
        ("X=1; echo ${X:+alt}", "alt"),
        ("X=hello; echo ${#X}", "5"),
        ("set -- a b c; echo ${#} ${#@}", "3 3"),
        ("f=pic.tar.gz; echo ${f%.gz}", "pic.tar"),
        ("f=pic.tar.gz; echo ${f%%.*}", "pic"),
        ("f=pic.tar.gz; echo ${f#*.}", "tar.gz"),
        ("f=pic.tar.gz; echo ${f##*.}", "gz"),
        ("f=a.b.c; echo ${f/./-}", "a-b.c"),
        ("f=a.b.c; echo ${f//./-}", "a-b-c"),
        ("f=abc; echo ${f/#a/X} ${f/%c/Y}", "Xbc abY"),
        ("f=abc; echo ${f/b}", "ac"),
        ("f=abcdef; echo ${f:2} ${f:1:3} ${f: -2}", "cdef bcd ef"),
        ("f=Hello; echo ${f^^} ${f,,} ${f,} ${f^}", "HELLO hello hello Hello"),
        ("echo ${X:-a b}", "a b"),
        ("Y=in; echo ${X:-$Y}", "in"),
        ("echo ${X:-'q q'}", "q q"),
        ("f=a-b; echo ${f%-*}-${f#*-}", "a-b"),
    ])
    def test_forms(self, run, script, out):
        assert run(script).out == out + "\n"

    def test_unset_error_form(self, run):
        r = run("echo ${X:?need X}; echo after")
        assert "X: need X" in r.err and "after" not in r.out

    def test_default_word_splits_when_unquoted(self, run):
        assert run('for w in ${X:-a b}; do echo [$w]; done').out == "[a]\n[b]\n"

    def test_default_word_stays_whole_in_quotes(self, run):
        assert run('for w in "${X:-a b}"; do echo [$w]; done').out == "[a b]\n"

    def test_bad_substitution(self, run):
        assert "bad substitution" in run("echo ${%bad}").err

    def test_nounset(self, run):
        r = run("set -u; echo $NOPE; echo after")
        assert "NOPE: unbound variable" in r.err and "after" not in r.out


class TestCase:
    @pytest.mark.parametrize("word,out", [
        ("a.png", "image"), ("b.txt", "text"), ("c.md", "text"), ("zzz", "other"),
    ])
    def test_patterns(self, run, word, out):
        script = (f'case {word} in *.png|*.jpg) echo image;; *.txt|*.md) echo text;; '
                  f'*) echo other;; esac')
        assert run(script).out == out + "\n"

    def test_first_match_wins(self, run):
        assert run("case a in a) echo 1;; a) echo 2;; esac").out == "1\n"

    def test_no_match_is_status_zero(self, run):
        assert run("case a in b) echo no;; esac; echo $?").out == "0\n"

    def test_multiline(self, run):
        script = "case $1 in\n  start)\n    echo going\n    ;;\n  stop) echo halt ;;\nesac"
        assert run("f() {\n" + script + "\n}\nf start; f stop").out == "going\nhalt\n"

    def test_leading_paren_and_character_classes(self, run):
        assert run("case b in (a|b) echo ab;; esac").out == "ab\n"
        assert run("case 7 in [0-9]) echo digit;; esac").out == "digit\n"
        assert run("case x in [!a-c]) echo notabc;; esac").out == "notabc\n"

    def test_quoted_pattern_is_literal(self, run):
        assert run("case '*' in '*') echo star;; esac").out == "star\n"
        assert run("case abc in '*') echo star;; *) echo other;; esac").out == "other\n"

    def test_variable_pattern(self, run):
        assert run("p=a*; case abc in $p) echo yes;; esac").out == "yes\n"

    def test_if_inside_an_arm(self, run):
        script = "case a in a) if true; then echo deep; fi;; esac"
        assert run(script).out == "deep\n"

    def test_last_arm_without_double_semicolon(self, run):
        assert run("case a in a) echo last\nesac").out == "last\n"

    def test_stray_double_semicolon(self, run):
        assert "syntax error" in run("echo a ;; echo b").err

    def test_missing_esac(self, run):
        assert run("case a in a) echo x;;").code != 0


class TestBraces:
    @pytest.mark.parametrize("script,out", [
        ("echo file.{txt,md}", "file.txt file.md"),
        ("echo {1..5}", "1 2 3 4 5"),
        ("echo {5..1}", "5 4 3 2 1"),
        ("echo {a..e}", "a b c d e"),
        ("echo {01..03}", "01 02 03"),
        ("echo {1..10..3}", "1 4 7 10"),
        ("echo x{,y}z", "xz xyz"),
        ("echo {a,b}{1,2}", "a1 a2 b1 b2"),
        ("echo {a,{b,c}}", "a b c"),
        ("echo {single}", "{single}"),
        ("echo {}", "{}"),
        ("echo '{a,b}' \"{a,b}\" \\{a,b}", "{a,b} {a,b} {a,b}"),
        ("echo {1..3}x", "1x 2x 3x"),
        ("echo ${X:-{a,b}}", "{a,b}"),
    ])
    def test_forms(self, run, script, out):
        assert run(script).out == out + "\n"

    def test_in_a_for_loop(self, run):
        assert run("for i in {1..3}; do echo n$i; done").out == "n1\nn2\nn3\n"

    def test_not_applied_to_assignments(self, run):
        assert run("X={a,b}; echo $X").out == "{a,b}\n"

    def test_creates_files(self, run, dirs):
        _, work = dirs
        run("touch f.{txt,md}")
        assert sorted(p.name for p in work.iterdir()) == ["f.md", "f.txt"]


class TestPortableBuiltinsInPipelines:
    def test_pipe_between_builtins(self, run):
        assert run("echo hello | tr a-z A-Z | head -n 1").out == "HELLO\n"

    def test_stderr_into_pipe(self, run):
        r = run("ls /no/such/folder 2>&1 | head -n 1")
        assert "no/such/folder" in r.out

    def test_command_substitution(self, run):
        assert run("echo I am $(echo here)").out == "I am here\n"


class TestShellOptions:
    def test_errexit_stops(self, run):
        r = run("set -e; echo a; false; echo b")
        assert r.out == "a\n" and r.code == 1

    def test_errexit_ignores_tested_commands(self, run):
        r = run("set -e; if false; then echo no; fi; false || echo ok; echo end")
        assert r.out == "ok\nend\n"

    def test_errexit_and_chain(self, run):
        assert run("set -e; false && echo no; echo still").out == "still\n"

    def test_xtrace(self, run):
        r = run("set -x; echo hi")
        assert "+ echo hi" in r.err and r.out == "hi\n"

    def test_set_o_lists(self, run):
        assert "errexit" in run("set -o").out

    def test_bad_option(self, run):
        assert run("set -Z").code != 0 or "invalid option" in run("set -Z").err
