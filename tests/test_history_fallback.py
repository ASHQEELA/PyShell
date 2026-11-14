"""History must work when readline is missing or incomplete, as on Windows."""
import subprocess
import sys

import pytest

from myshell import history
from myshell.builtins import builtin_history
from conftest import clean_env

STUB_INCOMPLETE = '''
# Looks like pyreadline3: most functions exist, remove_history_item does not.
_h = []
def get_current_history_length(): return len(_h)
def get_history_item(i): return _h[i - 1]
def add_history(line): _h.append(line)
def clear_history(): _h.clear()
def read_history_file(path=None): pass
def write_history_file(path=None): pass
def set_history_length(n): pass
def set_completer(f): pass
import builtins
_orig_input = builtins.input
def _recording_input(prompt=""):
    line = _orig_input(prompt)
    add_history(line)  # the real readline records what input() reads
    return line
builtins.input = _recording_input
def set_completer_delims(d): pass
def parse_and_bind(s): pass
def get_line_buffer(): return ""
def get_begidx(): return 0
'''
STUB_MISSING = None  # sys.modules["readline"] = None makes the import fail


@pytest.fixture
def fallback(monkeypatch):
    monkeypatch.setattr(history, "native", False)
    monkeypatch.setattr(history, "_entries", [])
    return history


def run(state, *args, capsys):
    builtin_history(state, list(args), sys.stdout, sys.stderr)
    cap = capsys.readouterr()
    return cap.out, cap.err


class TestOwnList:
    def test_add_length_item(self, fallback):
        fallback.add("one")
        fallback.add("two")
        assert fallback.length() == 2 and fallback.item(2) == "two"

    def test_entries_oldest_first(self, fallback):
        for w in ("a", "b", "c"):
            fallback.add(w)
        got = fallback.entries()
        assert got == ["a", "b", "c"]
        got.append("x")  # a copy
        assert fallback.length() == 3

    def test_use_builtin_switches_mode_and_keeps_readline_entries(self, monkeypatch):
        readline = pytest.importorskip("readline")
        monkeypatch.setattr(history, "native", True)
        monkeypatch.setattr(history, "_entries", [])
        readline.clear_history()
        readline.add_history("one")
        readline.add_history("two")
        try:
            assert history.entries() == ["one", "two"]
            history.use_builtin()
            assert history.native is False and history.records_input() is False
            assert history.entries() == ["one", "two"]
            history.add("three")
            assert history.item(3) == "three"
        finally:
            readline.clear_history()

    def test_input_is_not_recorded_by_input(self, fallback):
        assert fallback.records_input() is False

    def test_remove_and_clear(self, fallback):
        for w in ("a", "b", "c"):
            fallback.add(w)
        fallback.remove(2)
        assert [fallback.item(i) for i in (1, 2)] == ["a", "c"]
        fallback.forget_last()
        assert fallback.length() == 1
        fallback.clear()
        assert fallback.length() == 0

    def test_forget_last_on_empty_is_fine(self, fallback):
        fallback.forget_last()

    def test_limit(self, fallback):
        for i in range(1005):
            fallback.add(str(i))
        assert fallback.length() == 1000 and fallback.item(1) == "5"
        fallback.set_limit(10)
        assert fallback.length() == 10 and fallback.item(10) == "1004"

    def test_write_and_read(self, fallback, tmp_path):
        path = str(tmp_path / "h")
        fallback.add("echo one")
        fallback.add("two\nlines")
        fallback.write(path)
        fallback.clear()
        fallback.read(path)
        assert [fallback.item(1), fallback.item(2)] == ["echo one", "two lines"]

    def test_read_missing_file_raises_oserror(self, fallback, tmp_path):
        with pytest.raises(OSError):
            fallback.read(str(tmp_path / "none"))


class TestBuiltinInFallbackMode:
    @pytest.fixture(autouse=True)
    def entries(self, fallback):
        for w in ("one", "two", "three", "four"):
            fallback.add(w)

    def test_list_and_last_n(self, state, capsys):
        assert run(state, capsys=capsys)[0].count("\n") == 4
        assert run(state, "2", capsys=capsys)[0] == "    3  three\n    4  four\n"

    def test_delete_and_clear(self, state, capsys):
        run(state, "-d", "1", capsys=capsys)
        assert run(state, "1", capsys=capsys)[0] == "    3  four\n"
        run(state, "-c", capsys=capsys)
        assert run(state, capsys=capsys)[0] == ""

    def test_delete_out_of_range(self, state, capsys):
        assert "out of range" in run(state, "-d", "9", capsys=capsys)[1]

    def test_save_and_load_state_history(self, state, tmp_path):
        state.history_file = str(tmp_path / "hist")
        state.save_history()
        history.clear()
        state.load_history()
        assert history.length() == 4

    def test_load_with_no_file(self, state, tmp_path):
        state.history_file = str(tmp_path / "none")
        state.load_history()


LAUNCHER = """
import sys, types
stub = sys.argv[1]
if stub == "-":
    sys.modules["readline"] = None
else:
    mod = types.ModuleType("readline")
    exec(open(stub).read(), mod.__dict__)
    sys.modules["readline"] = mod
from myshell.cli import main
sys.exit(main(sys.argv[2:]))
"""


@pytest.mark.parametrize("stub", [STUB_INCOMPLETE, STUB_MISSING], ids=["incomplete", "missing"])
class TestWholeShellWithoutFullReadline:
    """A launcher puts a fake readline in sys.modules before the shell loads, because
    some Pythons have readline built in and a file on the path cannot replace it."""

    def start(self, dirs, tmp_path, stub, args, stdin=""):
        home, work = dirs
        if stub is None:
            stub_path = "-"
        else:
            stub_path = str(tmp_path / "fake_readline.py")
            (tmp_path / "fake_readline.py").write_text(stub)
        return subprocess.run([sys.executable, "-c", LAUNCHER, stub_path, *args], input=stdin,
                              cwd=work, env=clean_env(home), capture_output=True, text=True,
                              timeout=30)

    def test_the_fake_is_really_in_use(self, dirs, tmp_path, stub):
        home, work = dirs
        code = LAUNCHER.replace("from myshell.cli import main\nsys.exit(main(sys.argv[2:]))",
                                "from myshell import history\nprint(history.native, history._rl is None)")
        if stub is None:
            path = "-"
        else:
            path = str(tmp_path / "f.py")
            (tmp_path / "f.py").write_text(stub)
        p = subprocess.run([sys.executable, "-c", code, path], cwd=work, env=clean_env(home),
                           capture_output=True, text=True, timeout=30)
        assert p.stdout.strip() == ("False " + str(stub is None)), p.stderr

    def test_shell_starts_and_runs(self, dirs, tmp_path, stub):
        p = self.start(dirs, tmp_path, stub, ["-c", "echo ok"])
        assert p.stdout == "ok\n" and p.returncode == 0, p.stderr

    def test_guide_menu_does_not_crash(self, dirs, tmp_path, stub):
        p = self.start(dirs, tmp_path, stub, ["-c", "guide"], stdin="1\n\nq\n")
        assert "Traceback" not in p.stderr and "hello from myshell" in p.stdout

    def test_interactive_session_through_a_pipe(self, dirs, tmp_path, stub):
        p = self.start(dirs, tmp_path, stub, [], stdin="echo a\necho b\nhistory -d 1\nhistory\n")
        assert "Traceback" not in p.stderr, p.stderr
        assert "    1  echo b" in p.stdout and "    2  history -d 1" in p.stdout
        assert "echo a" not in p.stdout
