import pytest

from myshell.highlight import STYLES, tokenize_for_color


def kind_of(word):
    return "command" if word in ("echo", "ls", "cat", "grep", "cd") else "unknown"


def tok(text):
    return tokenize_for_color(text, kind_of)


def classes(text):
    """Fragments without blanks; plain runs are split into words."""
    out = []
    for cls, s in tok(text):
        if cls == "plain":
            out.extend(("plain", w) for w in s.split())
        elif s.strip():
            out.append((cls, s))
    return out


ODD = [
    "", " ", "\n", "echo 'open", 'echo "open $X', "echo $(", "echo $(ls (a", "echo `ls",
    "echo \\", "echo \\\\", "echo ${", "echo ${X", "echo $", "echo $$ $? $@ $1", "a | | b",
    "&&", "; ;;", "> >> < << <<<", "2>&1", "2>", "&>", "#", "# only comment", "echo a#b",
    "if", "if true; then", "for i in", "case x in a) echo;; esac", "function f {",
    "f() { ls; }", "A=1 B=2 ls", "A=", "=x", "echo \"a $(echo \"b\") c\"", "echo '\n'",
    "echo a\nls b\n# c\n", "\t\tls\t", "echo é中 \U0001f600", "((", "))", "{ }", "{",
    "echo ' \" '", 'echo " \' "', "ls |& cat", "x=$(y", "echo \"\\", "echo $(echo ')')",
]


@pytest.mark.parametrize("text", ODD)
def test_fragments_join_back(text):
    result = tok(text)
    assert "".join(s for _, s in result) == text
    assert all(c in STYLES for c, _ in result) and all(s for _, s in result)


def test_command_and_badcommand():
    assert classes("ls -l")[0] == ("command", "ls")
    assert classes("nosuch -l")[0] == ("badcommand", "nosuch")


def test_command_position_after_operators():
    for op in ["|", "||", "&&", ";", "&"]:
        got = classes("echo a %s nosuch" % op)
        assert got[-1] == ("badcommand", "nosuch"), op
        assert ("operator", op) in got


def test_argument_is_plain():
    assert classes("echo nosuch")[1] == ("plain", "nosuch")


def test_keywords_and_command_position_after_them():
    got = classes("if ls; then echo x; else cat; fi")
    assert ("keyword", "if") in got and ("keyword", "then") in got
    assert ("keyword", "else") in got and ("keyword", "fi") in got
    assert ("command", "ls") in got and ("command", "cat") in got
    assert classes("while nosuch; do ls; done")[1] == ("badcommand", "nosuch")


def test_keyword_as_argument_is_plain():
    assert classes("echo if done") == [("command", "echo"), ("plain", "if"), ("plain", "done")]


def test_for_in():
    got = classes("for i in 1 2; do echo $i; done")
    assert got[0] == ("keyword", "for") and got[1] == ("plain", "i")
    assert got[2] == ("keyword", "in")
    assert ("variable", "$i") in got


def test_function_and_braces():
    got = classes("function f { ls; }")
    assert got[0] == ("keyword", "function") and ("keyword", "{") in got
    assert ("command", "ls") in got and got[-1] == ("keyword", "}")


def test_strings_include_quotes():
    assert classes("echo 'a b'")[-1] == ("string", "'a b'")
    assert classes('echo "a b"')[-1] == ("string", '"a b"')
    assert classes("echo 'open")[-1] == ("string", "'open")


def test_variable_inside_double_quotes():
    got = classes('echo "hi $X!"')
    assert ("variable", "$X") in got
    assert got[1] == ("string", '"hi ') and got[-1] == ("string", '!"')


@pytest.mark.parametrize("var", ["$X", "${X}", "$1", "$@", "$?", "$$", "$HOME_DIR"])
def test_variables(var):
    assert classes("echo " + var)[-1] == ("variable", var)


@pytest.mark.parametrize("op", ["|", "||", "&&", ";", ";;", "&", ">", ">>", "<", "<<", "<<<",
                                "2>", "2>&1", "&>"])
def test_operators(op):
    assert ("operator", op) in classes("echo a %s b" % op)


def test_comment():
    got = classes("ls # a comment | x")
    assert got[-1] == ("comment", "# a comment | x")
    assert classes("echo a#b")[-1] == ("plain", "a#b")
    assert classes("echo a; # c")[-1] == ("comment", "# c")


def test_comment_ends_at_newline():
    assert classes("# c\nls") == [("comment", "# c"), ("command", "ls")]


def test_assignment_is_plain_and_keeps_command_position():
    got = classes("FOO=bar ls")
    assert got == [("plain", "FOO=bar"), ("command", "ls")]
    assert classes("FOO=$X ls")[1] == ("variable", "$X")


def test_newline_starts_a_command():
    assert classes("echo a\nnosuch")[-1] == ("badcommand", "nosuch")


def test_kind_of_raising_does_not_raise():
    def boom(word):
        raise RuntimeError
    assert "".join(s for _, s in tokenize_for_color("ls -l", boom)) == "ls -l"


def test_styles_table():
    assert set(STYLES) == {"command", "badcommand", "keyword", "string", "variable",
                           "operator", "comment", "plain"}
    assert STYLES["plain"] == "" and "bold" in STYLES["keyword"]
