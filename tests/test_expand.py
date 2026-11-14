import os

import pytest

from myshell import compat
from myshell.errors import ShellError
from myshell.expand import expand_tokens, expand_value, split_assignment
from myshell.lexer import lex


def ex(line, state):
    return expand_tokens(lex(line), state)


class TestVariables:
    def test_dollar_name_and_braces(self, state):
        state.set_variable("X", "1")
        assert ex("$X ${X} $X$X a${X}b", state) == ["1", "1", "11", "a1b"]

    def test_unset_variable_unquoted_disappears(self, state):
        assert ex("a $UNSET b", state) == ["a", "b"]

    def test_unset_variable_quoted_stays_as_an_empty_argument(self, state):
        assert ex('a "$UNSET" b', state) == ["a", "", "b"]

    def test_empty_quotes_stay(self, state):
        assert ex("a '' b", state) == ["a", "", "b"]

    def test_exit_status(self, state):
        state.last_status = 5
        assert ex("$? x$?", state) == ["5", "x5"]

    def test_process_id(self, state):
        assert ex("$$", state) == [str(os.getpid())]

    def test_single_quotes_block_expansion(self, state):
        state.set_variable("X", "1")
        assert ex("'$X' '${X}'", state) == ["$X", "${X}"]

    def test_double_quotes_allow_expansion(self, state):
        state.set_variable("X", "1")
        assert ex('"v=$X"', state) == ["v=1"]

    def test_escaped_dollar_stays_literal(self, state):
        state.set_variable("X", "1")
        assert ex(r'"\$X" \$X', state) == ["$X", "$X"]

    def test_lone_dollar_stays(self, state):
        assert ex("$ a$ $-", state) == ["$", "a$", "$-"]

    def test_environment_is_visible(self, state, monkeypatch):
        monkeypatch.setenv("FROM_ENV", "yes")
        assert ex("$FROM_ENV", state) == ["yes"]

    def test_shell_variable_wins_over_environment(self, state, monkeypatch):
        monkeypatch.setenv("DUP", "env")
        state.variables["DUP"] = "shell"
        assert ex("$DUP", state) == ["shell"]


class TestWordSplitting:
    def test_unquoted_expansion_splits_on_whitespace(self, state):
        state.set_variable("FLAGS", "-l  -a\t-h")
        assert ex("ls $FLAGS", state) == ["ls", "-l", "-a", "-h"]

    def test_quoted_expansion_does_not_split(self, state):
        state.set_variable("FLAGS", "-l -a")
        assert ex('"$FLAGS"', state) == ["-l -a"]

    def test_leading_and_trailing_space_make_no_empty_words(self, state):
        state.set_variable("V", "  a b  ")
        assert ex("$V", state) == ["a", "b"]

    def test_text_next_to_an_expansion_joins_the_edge_words(self, state):
        state.set_variable("V", "a b")
        assert ex("x${V}y", state) == ["xa", "by"]

    def test_literal_text_never_splits(self, state):
        assert ex(r"a\ b", state) == ["a b"]

    def test_expand_value_does_not_split_or_glob(self, state):
        state.set_variable("V", "a  b *")
        assert expand_value(lex("$V")[0], state) == "a  b *"


class TestGlobbing:
    @pytest.fixture(autouse=True)
    def files(self, state, dirs):
        _, work = dirs
        for name in ("a.txt", "b.txt", "c.md", ".hidden.txt"):
            (work / name).write_text("")

    def test_star_matches_in_sorted_order(self, state):
        assert ex("*.txt", state) == ["a.txt", "b.txt"]

    def test_no_match_keeps_the_pattern(self, state):
        assert ex("*.zip", state) == ["*.zip"]

    def test_question_mark_and_brackets(self, state):
        assert ex("?.md [ab].txt", state) == ["c.md", "a.txt", "b.txt"]

    def test_quoting_blocks_globbing(self, state):
        assert ex('"*.txt" \'*.txt\' \\*.txt', state) == ["*.txt", "*.txt", "*.txt"]

    def test_hidden_files_need_an_explicit_dot(self, state):
        assert ".hidden.txt" not in ex("*.txt", state)
        assert ex(".*.txt", state) == [".hidden.txt"]

    def test_pattern_from_a_variable_expands(self, state):
        state.set_variable("P", "*.md")
        assert ex("$P", state) == ["c.md"]

    def test_quoted_pattern_from_a_variable_does_not(self, state):
        state.set_variable("P", "*.md")
        assert ex('"$P"', state) == ["*.md"]


class TestTilde:
    def test_tilde_alone_and_with_path(self, state, dirs):
        home, _ = dirs
        assert ex("~ ~/x", state) == [str(home), f"{home}/x"]

    def test_quoted_tilde_stays(self, state):
        assert ex('"~" \'~\'', state) == ["~", "~"]

    def test_tilde_in_the_middle_stays(self, state):
        assert ex("a~b", state) == ["a~b"]


class TestAssignments:
    @pytest.mark.parametrize("word, name, value", [
        ("FOO=bar", "FOO", "bar"),
        ("FOO=", "FOO", ""),
        ("_x1=a=b", "_x1", "a=b"),
        ('FOO="a b"', "FOO", "a b"),
        ("FOO=$HOME", "FOO", None),
    ])
    def test_assignment_words(self, word, name, value, state):
        found = split_assignment(lex(word)[0])
        assert found is not None
        assert found[0] == name
        if value is not None:
            assert expand_value(found[1], state) == value

    @pytest.mark.parametrize("word", ["foo", "1A=x", "=x", "a-b=c", '"A=b"', "'A'=b"])
    def test_not_assignments(self, word):
        assert split_assignment(lex(word)[0]) is None


class TestCommandSubstitution:
    def test_output_becomes_words(self, state):
        assert ex("$(echo a b) c", state) == ["a", "b", "c"]

    def test_trailing_newlines_are_removed(self, state):
        assert ex('"$(printf "x\\n\\n\\n")"', state) == ["x"]

    def test_quoted_result_is_one_word(self, state):
        assert ex('"$(echo a   b)"', state) == ["a b"]

    def test_nested(self, state):
        assert ex("$(echo $(echo deep))", state) == ["deep"]

    def test_joins_with_surrounding_text(self, state):
        assert ex("pre$(echo MID)post", state) == ["preMIDpost"]

    def test_sees_shell_variables_and_aliases(self, state):
        state.set_variable("X", "val")
        state.aliases["hi"] = "echo aliased"
        assert ex("$(echo $X) $(hi)", state) == ["val", "aliased"]

    def test_changes_inside_do_not_leak_out(self, state):
        state.set_variable("X", "outer")
        assert ex("$(X=inner; echo $X) $X", state) == ["inner", "outer"]

    def test_empty_output_disappears_when_unquoted(self, state):
        assert ex("a $(true) b", state) == ["a", "b"]

    def test_works_without_fork(self, state, monkeypatch):
        monkeypatch.setattr(compat, "HAS_FORK", False)
        assert ex("$(echo hi)", state) == ["hi"]

    def test_without_fork_changes_do_not_leak_out(self, state, monkeypatch):
        monkeypatch.setattr(compat, "HAS_FORK", False)
        state.set_variable("X", "outer")
        assert ex("$(X=inner; echo $X) $X", state) == ["inner", "outer"]


class TestArithmetic:
    def test_basic(self, state):
        assert ex("$((1+2*3))", state) == ["7"]

    def test_variables_with_and_without_dollar(self, state):
        state.set_variable("x", "4")
        assert ex("$((x*2)) $(($x*2)) $(( x + 1 ))", state) == ["8", "8", "5"]

    def test_assignment_changes_the_shell_variable(self, state):
        ex("$((n=3, n*=2))", state)
        assert state.get_variable("n") == "6"

    def test_joins_with_text(self, state):
        assert ex("a$((1+1))b", state) == ["a2b"]

    def test_inside_double_quotes(self, state):
        assert ex('"v=$((2**5))"', state) == ["v=32"]

    def test_nested_parens_and_substitution(self, state):
        assert ex("$(( (1+2) * $(echo 4) ))", state) == ["12"]

    def test_nested_arithmetic(self, state):
        assert ex("$(( $((1+1)) + 1 ))", state) == ["3"]

    def test_two_groups_are_a_substitution_not_math(self, state, monkeypatch):
        from myshell import expand
        seen = []
        monkeypatch.setattr(expand, "command_substitution",
                            lambda text, st: seen.append(text) or "out")
        assert ex("$((a); (b))", state) == ["out"]
        assert seen == ["(a); (b)"]

    def test_errors_raise(self, state):
        with pytest.raises(ShellError, match="division by 0"):
            ex("$((1/0))", state)

    def test_unterminated(self, state):
        from myshell.errors import ShellSyntaxError
        with pytest.raises(ShellSyntaxError):
            ex("$((1+2)", state)


class TestBackticks:
    def test_output_becomes_words(self, state):
        assert ex("`echo a b` c", state) == ["a", "b", "c"]

    def test_quoted_is_one_word(self, state):
        assert ex('"`echo a   b`"', state) == ["a b"]

    def test_nested_with_escaped_backticks(self, state):
        assert ex(r"`echo \`echo deep\``", state) == ["deep"]

    def test_escaped_dollar_reaches_the_inner_command(self, state):
        state.set_variable("X", "val")
        assert ex(r"`echo \$X`", state) == ["val"]


class TestHeredocExpansion:
    def test_all_forms(self, state):
        from myshell.expand import expand_heredoc
        state.set_variable("x", "1")
        text = "$x ${x} $(echo c) `echo b` $((x+1)) \\$x \\\\ a\\\nb $ '$x'\n"
        assert expand_heredoc(text, state) == "1 1 c b 2 $x \\ ab $ '1'\n"
