import pytest

from myshell.errors import ShellSyntaxError
from myshell.lexer import AND, BG, OR, PIPE, REDIR, SEMI, WORD, lex


def words(line):
    return [t.text for t in lex(line) if t.kind == WORD]


def kinds(line):
    return [t.kind for t in lex(line)]


@pytest.mark.parametrize("line", ["", " ", "   \t  "])
def test_empty_input_gives_no_tokens(line):
    assert lex(line) == []


def test_plain_words():
    assert words("echo  hello   world") == ["echo", "hello", "world"]


class TestQuotes:
    def test_double_quotes_keep_spaces(self):
        assert words('echo "a   b"') == ["echo", "a   b"]

    def test_single_quotes_keep_everything(self):
        assert words("echo 'a \"b\" $HOME'") == ["echo", 'a "b" $HOME']

    def test_adjacent_parts_join_into_one_word(self):
        assert words('a"b c"d') == ["ab cd"]
        assert words("x'y z'w") == ["xy zw"]

    def test_empty_quotes_stay_as_an_empty_word(self):
        assert words('echo ""') == ["echo", ""]
        assert words("echo ''") == ["echo", ""]
        assert words('a"" b') == ["a", "b"]

    def test_parts_remember_their_quote_mode(self):
        assert lex('"a"b\'c\'')[0].parts == [("a", "double"), ("b", "plain"), ("c", "single")]

    def test_quoted_flag(self):
        assert lex("'a'")[0].quoted
        assert not lex("a")[0].quoted

    @pytest.mark.parametrize("line", ['echo "open', "echo 'open", 'echo "a\'b'])
    def test_unterminated_quote_is_an_error(self, line):
        with pytest.raises(ShellSyntaxError, match="unterminated quote"):
            lex(line)


class TestEscapes:
    def test_backslash_escapes_a_space(self):
        assert words(r"a\ b") == ["a b"]

    def test_backslash_escapes_an_operator(self):
        assert words(r"echo \| \&\& \>") == ["echo", "|", "&&", ">"]
        assert kinds(r"echo \|") == [WORD, WORD]

    def test_escaped_quote(self):
        assert words(r"echo \"hi\"") == ["echo", '"hi"']

    def test_inside_double_quotes_only_some_characters_escape(self):
        assert words(r'"a\"b"') == ['a"b']
        assert words(r'"a\\b"') == ["a\\b"]
        assert words(r'"a\nb"') == ["a\\nb"]  # \n is not special, the backslash stays

    def test_escaped_dollar_in_double_quotes_is_literal(self):
        token = lex(r'"\$HOME"')[0]
        assert token.text == "$HOME"
        assert ("$", "single") in token.parts  # marked literal so expansion skips it

    def test_trailing_backslash_is_kept(self):
        assert words("echo a\\") == ["echo", "a\\"]


class TestOperators:
    def test_operator_kinds(self):
        assert kinds("a && b || c ; d &") == [WORD, AND, WORD, OR, WORD, SEMI, WORD, BG]

    def test_operators_need_no_spaces(self):
        assert kinds("a|b") == [WORD, PIPE, WORD]
        assert kinds("a;b") == [WORD, SEMI, WORD]
        assert kinds("a&&b") == [WORD, AND, WORD]

    @pytest.mark.parametrize("quoted", ['"|"', "'&&'", '";"', "'>'"])
    def test_quoted_operators_are_plain_words(self, quoted):
        assert kinds(f"echo {quoted}") == [WORD, WORD]

    def test_operators_inside_quotes_do_not_split(self):
        toks = lex('echo "a | b && c ; d"')
        assert [t.kind for t in toks] == [WORD, WORD]
        assert toks[1].text == "a | b && c ; d"

    @pytest.mark.parametrize("op", [">", ">>", "2>", "2>>", "1>", "1>>", "&>", "&>>", "<", "<<<"])
    def test_redirect_operators_that_take_a_file(self, op):
        toks = lex(f"cmd {op} x")
        assert [(t.kind, t.text) for t in toks] == [(WORD, "cmd"), (REDIR, op), (WORD, "x")]

    @pytest.mark.parametrize("op", ["2>&1", "1>&2", ">&2", ">&1"])
    def test_redirect_operators_that_copy_a_stream(self, op):
        toks = lex(f"cmd {op}")
        assert [(t.kind, t.text) for t in toks] == [(WORD, "cmd"), (REDIR, op)]

    def test_longest_operator_wins(self):
        assert [t.text for t in lex("a &>> f") if t.kind == REDIR] == ["&>>"]
        assert [t.text for t in lex("a 2>> f") if t.kind == REDIR] == ["2>>"]
        assert [t.text for t in lex("a <<< x") if t.kind == REDIR] == ["<<<"]

    def test_digit_operators_only_count_at_the_start_of_a_word(self):
        toks = lex("echo a2>b")
        assert [(t.kind, t.text) for t in toks] == [
            (WORD, "echo"), (WORD, "a2"), (REDIR, ">"), (WORD, "b")]

    def test_digit_operator_at_word_start(self):
        assert [t.text for t in lex("echo 2>err") if t.kind == REDIR] == ["2>"]

    def test_redirect_without_spaces(self):
        assert [(t.kind, t.text) for t in lex("a>b")] == [
            (WORD, "a"), (REDIR, ">"), (WORD, "b")]


class TestComments:
    def test_comment_runs_to_the_end_of_the_line(self):
        assert words("echo hi # a comment | with && operators") == ["echo", "hi"]

    def test_whole_line_comment(self):
        assert lex("# only a comment") == []

    def test_hash_inside_a_word_is_not_a_comment(self):
        assert words("echo a#b") == ["echo", "a#b"]

    def test_hash_inside_quotes_is_not_a_comment(self):
        assert words('echo "# no" \'# no\'') == ["echo", "# no", "# no"]


class TestCommandSubstitution:
    def test_kept_whole_inside_a_word(self):
        assert words("echo $(echo a b)") == ["echo", "$(echo a b)"]

    def test_nested(self):
        assert words("echo $(echo $(echo x)) y") == ["echo", "$(echo $(echo x))", "y"]

    def test_parenthesis_inside_quotes_does_not_close_it(self):
        assert words('echo $(echo ")") z') == ["echo", '$(echo ")")', "z"]

    def test_inside_double_quotes(self):
        toks = lex('echo "[$(echo a b)]"')
        assert toks[1].parts == [("[$(echo a b)]", "double")]

    def test_operators_inside_are_not_split(self):
        assert kinds("echo $(a | b && c)") == [WORD, WORD]

    def test_unterminated(self):
        with pytest.raises(ShellSyntaxError, match="unterminated command substitution"):
            lex("echo $(echo")


class TestBackticks:
    def test_become_a_command_substitution(self):
        assert words("echo `echo hi`") == ["echo", "$(echo hi)"]

    def test_inside_double_quotes(self):
        toks = lex('echo "a `echo b` c"')
        assert toks[1].parts == [("a $(echo b) c", "double")]

    def test_single_quotes_keep_them_literal(self):
        assert words("echo '`x`'") == ["echo", "`x`"]

    def test_joins_with_neighbours(self):
        assert words("pre`x`post") == ["pre$(x)post"]

    def test_backslash_before_dollar_backtick_and_backslash_is_removed(self):
        assert words(r"echo `echo \$x \\ \`y\``") == ["echo", "$(echo $x \\ `y`)"]

    def test_other_backslashes_stay(self):
        assert words(r"echo `echo \n`") == ["echo", r"$(echo \n)"]

    def test_unterminated(self):
        with pytest.raises(ShellSyntaxError, match="unterminated command substitution"):
            lex("echo `ls")

    def test_paren_inside_backticks_does_not_confuse_dollar_paren(self):
        assert words("echo $(echo `echo ')'`)") == ["echo", "$(echo `echo ')'`)"]
