"""Alias expansion on tokens, before parsing."""
from typing import Dict, List

from .lexer import COMMAND_START_AFTER, WORD, Token, lex


# Runs before parsing, so an alias value may hold pipes, redirects and operators.
def expand_aliases(tokens: List[Token], aliases: Dict[str, str],
                   active: frozenset = frozenset()) -> List[Token]:
    result: List[Token] = []
    at_start = True
    for t in tokens:
        name = t.text
        if (at_start and t.kind == WORD and not t.quoted
                and name in aliases and name not in active):
            # Expand chains (a -> b -> ls). `active` blocks recursion (ls -> ls --color).
            result.extend(expand_aliases(lex(aliases[name]), aliases, active | {name}))
            at_start = False
            continue
        result.append(t)
        at_start = t.kind in COMMAND_START_AFTER
    return result
