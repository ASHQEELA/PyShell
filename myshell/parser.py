"""Parser: turns tokens into a tree of lists, pipelines and commands."""
import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple, Union

from .errors import ShellSyntaxError
from .lexer import AND, BG, DSEMI, OR, PIPE, REDIR, SEMI, WORD, Token


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
    pipeline: Union[Pipeline, "IfClause", "WhileClause", "ForClause", "CaseClause", "Group", "FuncDef"]


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
    """for NAME in WORDS; do BODY; done. Without `in`, words is None and the loop uses "$@"."""
    name: str
    words: Optional[List[Token]]
    body: List[ListItem]


@dataclass
class CaseClause:
    """case WORD in PATTERN|PATTERN) BODY ;; ... esac"""
    word: Token
    arms: List[Tuple[List[Token], List[ListItem]]]  # (patterns, body) pairs


@dataclass
class Group:
    """{ BODY; } runs in the current shell."""
    body: List[ListItem]


@dataclass
class FuncDef:
    """name() { BODY; }"""
    name: str
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
        elif isinstance(node, (ForClause, Group, FuncDef)):
            yield from iter_commands(node.body)
        elif isinstance(node, CaseClause):
            for _, body in node.arms:
                yield from iter_commands(body)
        else:
            yield from node.commands


def is_word(tok: Token, words) -> bool:
    """True for an unquoted word that is one of words."""
    return (tok.kind == WORD and len(tok.parts) == 1 and tok.parts[0][1] == "plain"
            and tok.parts[0][0] in words)


FUNC_HEADER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\(\)")


def ends_with_paren(tok: Token) -> bool:
    return bool(tok.parts) and tok.parts[-1][1] == "plain" and tok.parts[-1][0].endswith(")")


def compound_open(tokens: List[Token]) -> bool:
    """True when a block (if, while, until, for, case, { }) or a function header is not
    finished yet, so more lines are needed."""
    stack: List[str] = []  # "block", "case_head", "case_pat" or "case_body"
    start = True  # at the start of a command, where reserved words count
    pat_start = False  # at the start of a case pattern
    header = False  # the last token was a function header
    prev_start_word = None
    brace_ok = False  # a { here opens a function body
    function_kw = 0  # words left after the `function` keyword that may precede the body
    for t in tokens:
        top = stack[-1] if stack else None
        was_start = start
        header = False
        body_may_open = brace_ok
        brace_ok = False
        if t.kind != WORD:
            if t.kind == DSEMI and top == "case_body":
                stack[-1] = "case_pat"
                pat_start = True
                start = False
            elif t.kind == PIPE and top == "case_pat":
                start = False
            else:
                start = t.kind in (PIPE, AND, OR, SEMI, BG)
            prev_start_word = None
            continue
        if top == "case_head":
            if is_word(t, ("in",)):
                stack[-1] = "case_pat"
                pat_start = True
            start = False
        elif top == "case_pat":
            if pat_start and is_word(t, ("esac",)):
                stack.pop()
            elif ends_with_paren(t):
                stack[-1] = "case_body"
                start = True
                pat_start = False
            else:
                pat_start = False
        elif (start or (body_may_open and is_word(t, ("{",)))) and is_word(t, OPENERS):
            stack.append("case_head" if t.text == "case" else "block")
            start = t.text != "case"
        elif start and is_word(t, CLOSERS):
            if stack:
                stack.pop()
            start = False
        else:
            if was_start and t.parts[0][1] == "plain" and FUNC_HEADER_RE.fullmatch(t.text):
                header = brace_ok = True
            elif prev_start_word is not None and is_word(t, ("()",)):
                header = brace_ok = True
            elif was_start and is_word(t, ("function",)):
                function_kw = 2
            if function_kw and not header:
                function_kw -= 1
                brace_ok = True
            start = start and is_word(t, ("then", "else", "elif", "do"))
        prev_start_word = t if (was_start and not t.quoted and NAME_RE.fullmatch(t.text)) else None
    return bool(stack) or header


OPENERS = ("if", "while", "until", "for", "case", "{")
CLOSERS = ("fi", "done", "esac", "}")


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
            if self.at_word(stop) or self.peek().kind == DSEMI:
                break
            if self.at_funcdef():
                node = self.parse_funcdef()
            elif self.at_word(("case",)):
                node = self.parse_case()
            elif self.at_word(("{",)):
                node = self.parse_group()
            elif self.at_word(("if",)):
                node = self.parse_if()
            elif self.at_word(("while", "until")):
                node = self.parse_while()
            elif self.at_word(("for",)):
                node = self.parse_for()
            else:
                node = self.parse_pipeline()
            t = self.peek()
            if not isinstance(node, Pipeline) and t is not None and t.kind not in (AND, OR, SEMI, BG, DSEMI):
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
            if t is None or t.kind == DSEMI:
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
        words: Optional[List[Token]] = None
        if self.at_word(("in",)):
            self.pos += 1
            words = []
            while self.peek() is not None and self.peek().kind == WORD:
                words.append(self.peek())
                self.pos += 1
        if self.peek() is not None and self.peek().kind == SEMI:
            self.pos += 1
        self.expect_word("do")
        body = self.parse_block(("done",))
        self.expect_word("done")
        return ForClause(name, words, body)

    def skip_semis(self) -> None:
        while self.peek() is not None and self.peek().kind == SEMI:
            self.pos += 1

    def at_funcdef(self) -> bool:
        t = self.peek()
        if t is None or t.kind != WORD or t.quoted:
            return False
        if FUNC_HEADER_RE.fullmatch(t.text):
            return True
        nxt = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
        if t.text == "function":
            return nxt is not None and nxt.kind == WORD and not nxt.quoted
        return bool(NAME_RE.fullmatch(t.text) and nxt is not None and is_word(nxt, ("()",)))

    def parse_funcdef(self) -> FuncDef:
        t = self.peek()
        self.pos += 1
        if t.text == "function":
            name = self.peek().text
            self.pos += 1
            if self.peek() is not None and is_word(self.peek(), ("()",)):
                self.pos += 1
        elif FUNC_HEADER_RE.fullmatch(t.text):
            name = t.text[:-2]
        else:
            name = t.text
            self.pos += 1  # the "()" word
        name = name.rstrip("()")
        if not NAME_RE.fullmatch(name):
            raise ShellSyntaxError(f"`{name}': not a valid function name")
        self.skip_semis()
        if not self.at_word(("{",)):
            raise self.unexpected()
        return FuncDef(name, self.parse_group().body)

    def parse_group(self) -> Group:
        self.expect_word("{")
        body = self.parse_block(("}",))
        self.expect_word("}")
        return Group(body)

    def parse_case(self) -> CaseClause:
        self.pos += 1  # case
        word = self.peek()
        if word is None or word.kind != WORD:
            raise self.unexpected()
        self.pos += 1
        self.skip_semis()
        self.expect_word("in")
        self.skip_semis()
        arms: List[Tuple[List[Token], List[ListItem]]] = []
        while not self.at_word(("esac",)):
            patterns: List[Token] = []
            first = True
            while True:
                t = self.peek()
                if t is None or t.kind != WORD:
                    raise self.unexpected()
                self.pos += 1
                parts = list(t.parts)
                if first and parts[0][1] == "plain" and parts[0][0].startswith("("):
                    parts[0] = (parts[0][0][1:], "plain")
                closes = bool(parts) and parts[-1][1] == "plain" and parts[-1][0].endswith(")")
                if closes:
                    parts[-1] = (parts[-1][0][:-1], "plain")
                if any(text for text, _ in parts) or any(mode != "plain" for _, mode in parts):
                    patterns.append(Token(WORD, [p for p in parts if p[0] or p[1] != "plain"]))
                if closes:
                    break
                if self.peek() is not None and self.peek().kind == PIPE:
                    self.pos += 1
                    first = False
                    continue
                if self.peek() is not None and is_word(self.peek(), (")",)):
                    continue  # a lone ")" closes the patterns on the next round
                raise self.unexpected()
            if not patterns:
                raise self.unexpected()
            self.skip_semis()
            body = self.parse_list(("esac",))
            if self.peek() is not None and self.peek().kind == DSEMI:
                self.pos += 1
            self.skip_semis()
            arms.append((patterns, body))
        self.expect_word("esac")
        return CaseClause(word, arms)

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
    parser = Parser(tokens)
    items = parser.parse_list()
    if parser.peek() is not None:
        raise parser.unexpected()  # for example a stray ;;
    return items
