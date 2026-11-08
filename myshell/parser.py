"""Parser: turns tokens into a tree of lists, pipelines and commands."""
import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple, Union

from .errors import ShellSyntaxError
from .lexer import AND, BG, OR, PIPE, REDIR, SEMI, WORD, Token


NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# Parser: tokens -> syntax tree
#
#   list     := pipeline ( ('&&' | '||' | ';' | '&') pipeline )* [';' | '&']
#   pipeline := command ( '|' command )*
#   command  := ( WORD | REDIR WORD )+
# Redirections that copy one stream onto another and take no file name.
DUP_OPS = ("2>&1", "1>&2", ">&2", ">&1")


@dataclass
class Redirect:
    op: str
    target: Optional[Token] = None  # None for the DUP_OPS. The delimiter for << and <<-
    body: str = ""  # text of a here-document, filled in after parsing


@dataclass
class Command:
    argv: List[Token]
    redirects: List[Redirect] = field(default_factory=list)


@dataclass
class Pipeline:
    commands: List[Command]
    background: bool = False


@dataclass
class ListItem:
    op: str  # how this item links to the one before it: ';', '&&' or '||'
    pipeline: Union[Pipeline, "IfClause", "WhileClause", "ForClause"]


@dataclass
class IfClause:
    """if COND; then BODY; [elif COND; then BODY;] [else BODY;] fi"""
    clauses: List[Tuple[List[ListItem], List[ListItem]]]  # (condition, body) pairs
    else_body: Optional[List[ListItem]] = None


@dataclass
class WhileClause:
    """while COND; do BODY; done. With until=True the loop runs while COND fails."""
    cond: List[ListItem]
    body: List[ListItem]
    until: bool = False


@dataclass
class ForClause:
    """for NAME in WORDS; do BODY; done"""
    name: str
    words: List[Token]
    body: List[ListItem]


def iter_commands(items: List[ListItem]) -> Iterator[Command]:
    """Every simple command in the tree, in order, including those inside if blocks."""
    for item in items:
        node = item.pipeline
        if isinstance(node, IfClause):
            for cond, body in node.clauses:
                yield from iter_commands(cond)
                yield from iter_commands(body)
            if node.else_body:
                yield from iter_commands(node.else_body)
        elif isinstance(node, WhileClause):
            yield from iter_commands(node.cond)
            yield from iter_commands(node.body)
        elif isinstance(node, ForClause):
            yield from iter_commands(node.body)
        else:
            yield from node.commands


def is_word(tok: Token, words) -> bool:
    """True for an unquoted word that is one of words."""
    return (tok.kind == WORD and len(tok.parts) == 1 and tok.parts[0][1] == "plain"
            and tok.parts[0][0] in words)


def compound_open(tokens: List[Token]) -> bool:
    """True when an if, while, until or for block is not closed yet, so more lines are needed."""
    depth = 0
    start = True  # at the start of a command, where reserved words count
    for t in tokens:
        if t.kind != WORD:
            start = t.kind in (PIPE, AND, OR, SEMI, BG)
        elif start and is_word(t, OPENERS):
            depth += 1
        elif start and is_word(t, ("fi", "done")):
            depth -= 1
            start = False
        else:
            start = start and is_word(t, ("then", "else", "elif", "do"))
    return depth > 0


OPENERS = ("if", "while", "until", "for")


class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[Token]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def unexpected(self) -> ShellSyntaxError:
        t = self.peek()
        if t is None:
            return ShellSyntaxError("unexpected end of line")
        return ShellSyntaxError(f"unexpected token `{t.text}'")

    def at_word(self, words) -> bool:
        t = self.peek()
        return t is not None and is_word(t, words)

    def expect_word(self, word: str) -> None:
        if not self.at_word((word,)):
            raise self.unexpected()
        self.pos += 1

    def parse_list(self, stop=()) -> List[ListItem]:
        """Parse until the end of input or until a reserved word in `stop` starts a command."""
        items: List[ListItem] = []
        op = ";"
        while self.peek() is not None:
            if self.at_word(stop):
                break
            if self.at_word(("if",)):
                node = self.parse_if()
            elif self.at_word(("while", "until")):
                node = self.parse_while()
            elif self.at_word(("for",)):
                node = self.parse_for()
            else:
                node = self.parse_pipeline()
            t = self.peek()
            if not isinstance(node, Pipeline) and t is not None and t.kind not in (AND, OR, SEMI, BG):
                raise self.unexpected()  # no pipes or redirects after fi
            if t is not None and t.kind == BG:
                if not isinstance(node, Pipeline):
                    raise ShellSyntaxError("background compound commands are not supported")
                self.pos += 1
                node.background = True
                items.append(ListItem(op, node))
                op = ";"
                continue
            items.append(ListItem(op, node))
            if t is None:
                break
            self.pos += 1  # consume && || or ;
            op = t.text
            if self.peek() is None:
                if op in ("&&", "||"):
                    raise ShellSyntaxError("unexpected end of line")
                break  # trailing ';'
        return items

    def parse_block(self, stop) -> List[ListItem]:
        items = self.parse_list(stop)
        if not items:
            raise self.unexpected()
        return items

    def parse_if(self) -> IfClause:
        self.pos += 1  # if
        clauses = []
        while True:
            cond = self.parse_block(("then",))
            self.expect_word("then")
            body = self.parse_block(("elif", "else", "fi"))
            clauses.append((cond, body))
            if self.at_word(("elif",)):
                self.pos += 1
                continue
            break
        else_body = None
        if self.at_word(("else",)):
            self.pos += 1
            else_body = self.parse_block(("fi",))
        self.expect_word("fi")
        return IfClause(clauses, else_body)

    def parse_while(self) -> WhileClause:
        until = self.tokens[self.pos].text == "until"
        self.pos += 1
        cond = self.parse_block(("do",))
        self.expect_word("do")
        body = self.parse_block(("done",))
        self.expect_word("done")
        return WhileClause(cond, body, until)

    def parse_for(self) -> ForClause:
        self.pos += 1  # for
        t = self.peek()
        if t is None or t.kind != WORD or t.quoted or not NAME_RE.fullmatch(t.text):
            raise self.unexpected()
        name = t.text
        self.pos += 1
        words: List[Token] = []
        if self.at_word(("in",)):
            self.pos += 1
            while self.peek() is not None and self.peek().kind == WORD:
                words.append(self.peek())
                self.pos += 1
        if self.peek() is not None and self.peek().kind == SEMI:
            self.pos += 1
        self.expect_word("do")
        body = self.parse_block(("done",))
        self.expect_word("done")
        return ForClause(name, words, body)

    def parse_pipeline(self) -> Pipeline:
        commands = [self.parse_command()]
        while self.peek() is not None and self.peek().kind == PIPE:
            self.pos += 1
            commands.append(self.parse_command())
        return Pipeline(commands)

    def parse_command(self) -> Command:
        argv: List[Token] = []
        redirects: List[Redirect] = []
        while True:
            t = self.peek()
            if t is None or t.kind not in (WORD, REDIR):
                break
            self.pos += 1
            if t.kind == WORD:
                argv.append(t)
                continue
            if t.text in DUP_OPS:
                redirects.append(Redirect(t.text))
                continue
            target = self.peek()
            if target is None or target.kind != WORD:
                raise self.unexpected()
            self.pos += 1
            redirects.append(Redirect(t.text, target))
        if not argv and not redirects:
            raise self.unexpected()
        return Command(argv, redirects)


def parse(tokens: List[Token]) -> List[ListItem]:
    return Parser(tokens).parse_list()
