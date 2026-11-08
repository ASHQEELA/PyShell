import re

import pytest

from myshell.arith import MAX_DEPTH, evaluate, wrap
from myshell.errors import ShellError


@pytest.fixture
def env():
    values = {}
    return values, values.get, values.__setitem__


def ev(expr, env_=None):
    values = {} if env_ is None else env_
    return evaluate(expr, values.get, values.__setitem__)


@pytest.mark.parametrize("expr, expected", [
    ("", 0), ("  ", 0), ("42", 42), ("0x1F", 31), ("010", 8),
    ("1+2*3", 7), ("(1+2)*3", 9), ("10-3-2", 5), ("2**3**2", 512),
    ("-2**2", 4), ("7/2", 3), ("-7/2", -3), ("7%3", 1), ("-7%3", -1), ("7%-3", 1),
    ("1<<4", 16), ("256>>4", 16), ("6&3", 2), ("6|3", 7), ("6^3", 5),
    ("!0", 1), ("!5", 0), ("~0", -1), ("+4", 4), ("- -4", 4),
    ("1<2", 1), ("2<=1", 0), ("3>2", 1), ("2>=3", 0), ("2==2", 1), ("2!=2", 0),
    ("1&&0", 0), ("1&&2", 1), ("0||0", 0), ("0||3", 1),
    ("1?10:20", 10), ("0?10:20", 20), ("0?1:0?2:3", 3),
    ("1,2,3", 3),
])
def test_values(expr, expected):
    assert ev(expr) == expected


def test_wraps_to_64_bits():
    assert wrap(2 ** 63) == -(2 ** 63)
    assert wrap(-(2 ** 63) - 1) == 2 ** 63 - 1
    assert ev("9223372036854775807 + 1") == -9223372036854775808
    assert ev("2**64") == 0
    assert ev("1<<63") == -9223372036854775808


def test_variables():
    values = {"x": "5", "y": "", "e": "2+3"}
    assert ev("x*2", values) == 10
    assert ev("y+1", values) == 1
    assert ev("unset+1", values) == 1
    assert ev("e*2", values) == 10  # a variable may hold an expression


def test_assignment_operators():
    values = {}
    assert ev("a=5", values) == 5 and values["a"] == "5"
    assert ev("a+=3", values) == 8 and values["a"] == "8"
    assert ev("a*=2", values) == 16
    assert ev("a-=6", values) == 10
    assert ev("a/=3", values) == 3
    assert ev("a%=2", values) == 1
    assert ev("a<<=4", values) == 16
    assert ev("a>>=2", values) == 4
    assert ev("a&=6", values) == 4
    assert ev("a|=1", values) == 5
    assert ev("a^=1", values) == 4
    assert ev("b=c=7", values) == 7 and values["c"] == "7"


def test_short_circuit_skips_side_effects():
    values = {}
    ev("0 && (a=1)", values)
    ev("1 || (b=1)", values)
    ev("1 ? 0 : (c=1)", values)
    assert values == {}


def test_comma_runs_both_sides():
    values = {}
    assert ev("a=1, b=a+1", values) == 2
    assert values == {"a": "1", "b": "2"}


@pytest.mark.parametrize("expr, message", [
    ("1/0", "division by 0"), ("1%0", "division by 0"),
    ("2**-1", "exponent less than 0"),
    ("1+", "operand expected"), ("(1", "missing `)'"), ("1 2", "unexpected token"),
    ("08", "invalid number"), ("0xZ", "invalid number"), ("1 $ 2", "invalid character"),
    ("1?2", "`:' expected"), ("5=3", "non-variable"), ("a b", "unexpected token"),
])
def test_errors(expr, message):
    with pytest.raises(ShellError, match=re.escape(message)):
        ev(expr)


def test_self_reference_stops():
    values = {"a": "a+1"}
    with pytest.raises(ShellError, match="recursion"):
        ev("a", values)
    assert MAX_DEPTH > 1


def test_huge_exponent_is_fast():
    assert ev("3**1000000000") == wrap(pow(3, 1000000000, 2 ** 64))
