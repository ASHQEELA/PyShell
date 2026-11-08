from myshell.aliases import expand_aliases
from myshell.lexer import PIPE, WORD, lex


def expand(line, aliases):
    return expand_aliases(lex(line), aliases)


def texts(tokens):
    return [t.text for t in tokens]


def test_simple_alias():
    assert texts(expand("ll", {"ll": "ls -l"})) == ["ls", "-l"]


def test_arguments_after_the_alias_are_kept():
    assert texts(expand("ll /tmp", {"ll": "ls -l"})) == ["ls", "-l", "/tmp"]


def test_only_the_command_word_expands():
    assert texts(expand("echo ll", {"ll": "ls -l"})) == ["echo", "ll"]


def test_aliases_chain():
    aliases = {"a": "b", "b": "c -x", "c": "ls"}
    assert texts(expand("a file", aliases)) == ["ls", "-x", "file"]


def test_an_alias_cannot_expand_itself():
    assert texts(expand("ls", {"ls": "ls --color"})) == ["ls", "--color"]


def test_mutual_recursion_stops():
    assert texts(expand("a", {"a": "b", "b": "a"})) == ["a"]


def test_quoted_command_is_not_expanded():
    assert texts(expand('"ll"', {"ll": "ls -l"})) == ["ll"]
    assert texts(expand("'ll'", {"ll": "ls -l"})) == ["ll"]


def test_commands_after_operators_expand():
    aliases = {"ll": "ls -l"}
    assert texts(expand("echo x | ll", aliases)) == ["echo", "x", "|", "ls", "-l"]
    assert texts(expand("true && ll", aliases)) == ["true", "&&", "ls", "-l"]
    assert texts(expand("true; ll", aliases)) == ["true", ";", "ls", "-l"]


def test_alias_value_may_hold_a_pipeline():
    tokens = expand("both", {"both": "echo a | cat"})
    assert [t.kind for t in tokens] == [WORD, WORD, PIPE, WORD]


def test_no_aliases_leaves_tokens_alone():
    assert texts(expand("echo hi", {})) == ["echo", "hi"]
