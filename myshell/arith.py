"""Arithmetic expansion: evaluates the text inside $((...)).

Integer math with 64-bit wraparound, as in bash. Supported:
  numbers        42  0x1F (hex)  010 (octal)
  variables      x  (unset or empty means 0)
  unary          + - ! ~
  binary         ** * / % + - << >> < <= > >= == != & ^ | && ||
  conditional    a ? b : c
  assignment     = += -= *= /= %= <<= >>= &= ^= |=
  comma          a, b
  grouping       ( ... )
Division truncates toward zero. && || and ?: skip the side that is not needed."""
import re
from typing import Callable, List, Optional, Tuple

from .errors import ShellError

GetVar = Callable[[str], Optional[str]]
SetVar = Callable[[str, str], None]

MAX_DEPTH = 16  # a variable may hold an expression that names other variables

_MASK = (1 << 64) - 1


def wrap(n: int) -> int:
    """Fit n into a signed 64-bit integer."""
    n &= _MASK
    return n - (1 << 64) if n >> 63 else n


_TOKEN_RE = re.compile(r"""
    \s*(?:
        (?P<num>[0-9][A-Za-z_0-9]*)
      | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
      | (?P<op><<=|>>=|\*\*|<<|>>|<=|>=|==|!=|&&|\|\||\+=|-=|\*=|/=|%=|&=|\^=|\|=|[-+*/%<>&^|!~?:(),=])
    )""", re.VERBOSE)

_ASSIGN_OPS = ("=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "^=", "|=")

# Binary operators from the loosest binding to the tightest.
_LEVELS = [
    ("||",), ("&&",), ("|",), ("^",), ("&",), ("==", "!="),
    ("<", "<=", ">", ">="), ("<<", ">>"), ("+", "-"), ("*", "/", "%"),
]

Token = Tuple[str, object]


def _error(expr: str, message: str) -> ShellError:
    return ShellError(f"{expr.strip()}: {message}")


def _number(text: str, expr: str) -> int:
    try:
        if text[:2] in ("0x", "0X"):
            return int(text[2:], 16)
        if text.isdigit():
            if len(text) > 1 and text[0] == "0":
                return int(text, 8)  # a leading zero means octal
            return int(text)
    except ValueError:
        pass
    raise _error(expr, f"invalid number `{text}'")


def _tokenize(expr: str) -> List[Token]:
    tokens: List[Token] = []
    pos = 0
    while True:
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            if expr[pos:].strip() == "":
                return tokens
            bad = expr[pos:].lstrip()[0]
            raise _error(expr, f"syntax error: invalid character `{bad}'")
        if m.group("num") is not None:
            tokens.append(("num", _number(m.group("num"), expr)))
        elif m.group("name") is not None:
            tokens.append(("name", m.group("name")))
        else:
            tokens.append(("op", m.group("op")))
        pos = m.end()


class _Parser:
    """Builds a tree of tuples from the tokens. Precedence climbs from comma down to
    primary, one method per level."""

    def __init__(self, tokens: List[Token], expr: str):
        self.tokens = tokens
        self.expr = expr
        self.pos = 0

    def peek_op(self) -> Optional[str]:
        if self.pos < len(self.tokens) and self.tokens[self.pos][0] == "op":
            return self.tokens[self.pos][1]
        return None

    def accept(self, *ops: str) -> Optional[str]:
        op = self.peek_op()
        if op is not None and op in ops:
            self.pos += 1
            return op
        return None

    def fail(self, message: str) -> ShellError:
        return _error(self.expr, f"syntax error: {message}")

    def parse(self):
        node = self.comma()
        if self.pos < len(self.tokens):
            raise self.fail(f"unexpected token `{self.tokens[self.pos][1]}'")
        return node

    def comma(self):
        node = self.assignment()
        while self.accept(","):
            node = ("comma", node, self.assignment())
        return node

    def assignment(self):
        left = self.conditional()
        op = self.peek_op()
        if op in _ASSIGN_OPS:
            if left[0] != "var":
                raise self.fail("attempted assignment to a non-variable")
            self.pos += 1
            return ("assign", op, left[1], self.assignment())
        return left

    def conditional(self):
        cond = self.binary(0)
        if self.accept("?"):
            yes = self.assignment()
            if not self.accept(":"):
                raise self.fail("`:' expected for conditional expression")
            return ("cond", cond, yes, self.conditional())
        return cond

    def binary(self, level: int):
        if level == len(_LEVELS):
            return self.power()
        node = self.binary(level + 1)
        while True:
            op = self.accept(*_LEVELS[level])
            if op is None:
                return node
            right = self.binary(level + 1)
            if op == "&&":
                node = ("and", node, right)
            elif op == "||":
                node = ("or", node, right)
            else:
                node = ("bin", op, node, right)

    def power(self):
        base = self.unary()
        if self.accept("**"):
            return ("bin", "**", base, self.power())  # right to left
        return base

    def unary(self):
        op = self.accept("+", "-", "!", "~")
        if op:
            return ("un", op, self.unary())
        return self.primary()

    def primary(self):
        if self.pos >= len(self.tokens):
            raise self.fail("operand expected")
        kind, value = self.tokens[self.pos]
        if kind == "num":
            self.pos += 1
            return ("num", value)
        if kind == "name":
            self.pos += 1
            return ("var", value)
        if value == "(":
            self.pos += 1
            node = self.comma()
            if not self.accept(")"):
                raise self.fail("missing `)'")
            return node
        raise self.fail(f"operand expected (error token is `{value}')")


class _Evaluator:
    def __init__(self, expr: str, get_var: GetVar, set_var: SetVar, depth: int):
        self.expr = expr
        self.get_var = get_var
        self.set_var = set_var
        self.depth = depth

    def variable(self, name: str) -> int:
        raw = self.get_var(name)
        if raw is None or raw.strip() == "":
            return 0
        if self.depth >= MAX_DEPTH:
            raise _error(self.expr, "expression recursion level exceeded")
        # The value may be a number or another expression.
        return evaluate(raw, self.get_var, self.set_var, self.depth + 1)

    def run(self, node) -> int:
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "var":
            return self.variable(node[1])
        if kind == "un":
            value = self.run(node[2])
            return {"+": value, "-": wrap(-value), "!": int(value == 0),
                    "~": ~value}[node[1]]
        if kind == "bin":
            return self.binary(node[1], self.run(node[2]), self.run(node[3]))
        if kind == "and":
            return int(self.run(node[1]) != 0 and self.run(node[2]) != 0)
        if kind == "or":
            return int(self.run(node[1]) != 0 or self.run(node[2]) != 0)
        if kind == "cond":
            return self.run(node[2]) if self.run(node[1]) != 0 else self.run(node[3])
        if kind == "comma":
            self.run(node[1])
            return self.run(node[2])
        if kind == "assign":
            _, op, name, right = node
            value = self.run(right)
            if op != "=":
                value = self.binary(op[:-1], self.variable(name), value)
            self.set_var(name, str(value))
            return value
        raise AssertionError(kind)

    def binary(self, op: str, a: int, b: int) -> int:
        if op == "+":
            return wrap(a + b)
        if op == "-":
            return wrap(a - b)
        if op == "*":
            return wrap(a * b)
        if op in ("/", "%"):
            if b == 0:
                raise _error(self.expr, "division by 0")
            quotient = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                quotient = -quotient  # truncate toward zero, as in C
            return wrap(quotient) if op == "/" else wrap(a - quotient * b)
        if op == "**":
            if b < 0:
                raise _error(self.expr, "exponent less than 0")
            return wrap(pow(a, b, 1 << 64))  # modular, so huge exponents stay fast
        if op == "<<":
            return wrap(a << (b & 63))
        if op == ">>":
            return a >> (b & 63)
        if op == "&":
            return a & b
        if op == "^":
            return a ^ b
        if op == "|":
            return a | b
        return int({"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b,
                    "==": a == b, "!=": a != b}[op])


def evaluate(expr: str, get_var: GetVar, set_var: SetVar, depth: int = 0) -> int:
    """Evaluate an arithmetic expression. An empty expression is 0.
    Raises ShellError for syntax errors, division by zero and similar."""
    tokens = _tokenize(expr)
    if not tokens:
        return 0
    tree = _Parser(tokens, expr).parse()
    return _Evaluator(expr, get_var, set_var, depth).run(tree)
