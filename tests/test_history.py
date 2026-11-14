import pytest

readline = pytest.importorskip("readline")  # Windows has no readline

from myshell.builtins import builtin_history


@pytest.fixture
def hist():
    readline.clear_history()
    for line in ("one", "two", "three", "four"):
        readline.add_history(line)
    yield
    readline.clear_history()


def run(state, *args, capsys):
    status = builtin_history(state, list(args), __import__("sys").stdout, __import__("sys").stderr)
    cap = capsys.readouterr()
    return status, cap.out, cap.err


def test_lists_all_with_numbers(state, hist, capsys):
    status, out, _ = run(state, capsys=capsys)
    assert status == 0
    assert out == "    1  one\n    2  two\n    3  three\n    4  four\n"


def test_last_n(state, hist, capsys):
    assert run(state, "2", capsys=capsys)[1] == "    3  three\n    4  four\n"


def test_zero_prints_nothing(state, hist, capsys):
    assert run(state, "0", capsys=capsys)[1] == ""


def test_n_larger_than_history_prints_all(state, hist, capsys):
    assert run(state, "99", capsys=capsys)[1].count("\n") == 4


def test_clear(state, hist, capsys):
    assert run(state, "-c", capsys=capsys)[0] == 0
    assert readline.get_current_history_length() == 0


def test_delete_one(state, hist, capsys):
    assert run(state, "-d", "2", capsys=capsys)[0] == 0
    assert run(state, capsys=capsys)[1] == "    1  one\n    2  three\n    3  four\n"


@pytest.mark.parametrize("pos", ["0", "5"])
def test_delete_out_of_range(state, hist, capsys, pos):
    status, _, err = run(state, "-d", pos, capsys=capsys)
    assert status == 1 and "history position out of range" in err
    assert readline.get_current_history_length() == 4


def test_delete_needs_a_number(state, hist, capsys):
    assert "numeric argument required" in run(state, "-d", "x", capsys=capsys)[2]
    assert run(state, "-d", capsys=capsys)[0] == 2


def test_write_and_read_a_file(state, hist, tmp_path, capsys):
    path = str(tmp_path / "h.txt")
    assert run(state, "-w", path, capsys=capsys)[0] == 0
    readline.clear_history()
    assert run(state, "-r", path, capsys=capsys)[0] == 0
    assert run(state, "1", capsys=capsys)[1] == "    4  four\n"


def test_write_defaults_to_the_history_file(state, hist, tmp_path, capsys):
    state.history_file = str(tmp_path / "default_history")
    run(state, "-w", capsys=capsys)
    with open(state.history_file) as f:
        assert f.read().split()[-4:] == ["one", "two", "three", "four"]


def test_read_missing_file(state, hist, tmp_path, capsys):
    status, _, err = run(state, "-r", str(tmp_path / "nope"), capsys=capsys)
    assert status == 1 and "No such file" in err


def test_errors(state, hist, capsys):
    assert run(state, "-x", capsys=capsys)[0] == 2
    assert "invalid option" in run(state, "-x", capsys=capsys)[2]
    assert "numeric argument required" in run(state, "abc", capsys=capsys)[2]
    assert "too many arguments" in run(state, "1", "2", capsys=capsys)[2]


def test_history_length_limit(state, tmp_path):
    state.history_file = str(tmp_path / "none")
    state.load_history()
    assert readline.get_history_length() == 1000
