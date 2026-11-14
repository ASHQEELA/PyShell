"""The built-in ls, cat, head, tr, sleep, mkdir, rm, cp, mv, touch, wc and grep.
They run in the process, so the tests call them directly."""
import io

import pytest

from myshell.coreutils import PORTABLE


@pytest.fixture
def call(state):
    def run(name, *args, stdin=""):
        state.stdin = io.StringIO(stdin)
        out, err = io.StringIO(), io.StringIO()
        status = PORTABLE[name](state, list(args), out, err)
        return status, out.getvalue(), err.getvalue()
    return run


def write(path, text):
    path.write_text(text)
    return str(path)


def test_the_table_has_every_command():
    assert set(PORTABLE) == set("ls cat head tr sleep mkdir rm cp mv touch wc grep".split())


class TestLs:
    def test_lists_sorted_without_hidden(self, call, dirs):
        _, work = dirs
        for n in ("b", "A", ".hid", "c"):
            (work / n).write_text("")
        assert call("ls")[1] == "A\nb\nc\n"

    def test_all_shows_hidden(self, call, dirs):
        _, work = dirs
        (work / ".hid").write_text("")
        assert ".hid" in call("ls", "-a")[1]

    def test_long_format_shape(self, call, dirs):
        _, work = dirs
        (work / "f").write_text("abc")
        line = call("ls", "-l")[1].strip()
        assert line.endswith(" f") and " 3 " in line and line[0] in "-d"

    def test_missing_path(self, call):
        status, _, err = call("ls", "nope")
        assert status == 2 and "cannot access 'nope'" in err

    def test_folder_argument_and_headings(self, call, dirs):
        _, work = dirs
        (work / "d").mkdir()
        (work / "d" / "x").write_text("")
        (work / "y").write_text("")
        out = call("ls", "d", "y")[1]
        assert "y" in out and "d:" in out and "x" in out

    def test_d_flag(self, call, dirs):
        _, work = dirs
        (work / "d").mkdir()
        (work / "d" / "x").write_text("")
        assert call("ls", "-d", "d")[1] == "d\n"

    def test_bad_option(self, call):
        assert call("ls", "-Z")[0] == 2


class TestCat:
    def test_files_and_stdin(self, call, dirs):
        _, work = dirs
        a = write(work / "a", "1\n2\n")
        assert call("cat", a, a)[1] == "1\n2\n1\n2\n"
        assert call("cat", stdin="in\n")[1] == "in\n"
        assert call("cat", "-", stdin="dash\n")[1] == "dash\n"

    def test_numbering(self, call):
        assert call("cat", "-n", stdin="a\nb\n")[1] == "     1\ta\n     2\tb\n"

    def test_missing_file_keeps_going(self, call, dirs):
        _, work = dirs
        a = write(work / "a", "ok\n")
        status, out, err = call("cat", "nope", a)
        assert status == 1 and out == "ok\n" and "nope: No such file" in err


class TestHead:
    TEXT = "".join(f"{i}\n" for i in range(1, 15))

    def test_default_ten(self, call):
        assert call("head", stdin=self.TEXT)[1].count("\n") == 10

    @pytest.mark.parametrize("args", [("-n", "3"), ("-n3",), ("-3",)])
    def test_counts(self, call, args):
        assert call("head", *args, stdin=self.TEXT)[1] == "1\n2\n3\n"

    def test_several_files_get_headers(self, call, dirs):
        _, work = dirs
        a, b = write(work / "a", "1\n"), write(work / "b", "2\n")
        out = call("head", a, b)[1]
        assert f"==> {a} <==" in out and f"==> {b} <==" in out


class TestTr:
    @pytest.mark.parametrize("args,text,out", [
        (("a-z", "A-Z"), "hello", "HELLO"),
        (("[:lower:]", "[:upper:]"), "abC", "ABC"),
        (("abc", "x"), "aabbcc", "xxxxxx"),
        (("-d", "l"), "hello", "heo"),
        (("-d", "[:digit:]"), "a1b2", "ab"),
        (("-s", " "), "a   b  c", "a b c"),
        (("-cd", "a-z"), "a1b2", "ab"),
        (("\\n", " "), "a\nb", "a b"),
    ])
    def test_cases(self, call, args, text, out):
        assert call("tr", *args, stdin=text)[1] == out

    def test_missing_operand(self, call):
        assert call("tr")[0] != 0


class TestSleep:
    def test_short_sleep(self, call):
        assert call("sleep", "0.01")[0] == 0

    def test_suffix_and_sum(self, call, monkeypatch):
        seen = []
        monkeypatch.setattr("myshell.coreutils.time.sleep", seen.append)
        call("sleep", "1m", "2s")
        assert seen == [62.0]

    def test_bad_value(self, call):
        status, _, err = call("sleep", "abc")
        assert status == 1 and "invalid time interval" in err

    def test_missing_operand(self, call):
        assert call("sleep")[0] == 1


class TestMkdirTouchRm:
    def test_mkdir_and_parents(self, call, dirs):
        _, work = dirs
        assert call("mkdir", "a")[0] == 0
        assert call("mkdir", "a")[0] == 1
        assert call("mkdir", "-p", "a", "b/c/d")[0] == 0
        assert (work / "b" / "c" / "d").is_dir()

    def test_touch_creates_and_keeps(self, call, dirs):
        _, work = dirs
        call("touch", "t")
        assert (work / "t").exists()
        call("touch", "-c", "never")
        assert not (work / "never").exists()

    def test_rm_file_and_folder_rules(self, call, dirs):
        _, work = dirs
        (work / "f").write_text("")
        (work / "d").mkdir()
        (work / "d" / "x").write_text("")
        assert call("rm", "f")[0] == 0 and not (work / "f").exists()
        status, _, err = call("rm", "d")
        assert status == 1 and "Is a directory" in err
        assert call("rm", "-r", "d")[0] == 0 and not (work / "d").exists()

    def test_rm_force_ignores_missing(self, call):
        assert call("rm", "-f", "nope")[0] == 0
        assert call("rm", "nope")[0] == 1

    def test_rm_refuses_root_and_home(self, call, dirs):
        home, _ = dirs
        assert call("rm", "-rf", "/")[0] == 1
        assert call("rm", "-rf", str(home))[0] == 1
        assert home.exists()


class TestCpMv:
    def test_cp_file_and_into_folder(self, call, dirs):
        _, work = dirs
        write(work / "a", "data")
        (work / "d").mkdir()
        assert call("cp", "a", "b")[0] == 0
        assert call("cp", "a", "b", "d")[0] == 0
        assert (work / "b").read_text() == "data"
        assert (work / "d" / "a").read_text() == "data"

    def test_cp_folder_needs_r(self, call, dirs):
        _, work = dirs
        (work / "d").mkdir()
        (work / "d" / "x").write_text("1")
        status, _, err = call("cp", "d", "e")
        assert status == 1 and "-r not specified" in err
        assert call("cp", "-r", "d", "e")[0] == 0
        assert (work / "e" / "x").read_text() == "1"

    def test_cp_no_clobber(self, call, dirs):
        _, work = dirs
        write(work / "a", "new")
        write(work / "b", "old")
        call("cp", "-n", "a", "b")
        assert (work / "b").read_text() == "old"

    def test_mv_rename_and_into_folder(self, call, dirs):
        _, work = dirs
        write(work / "a", "x")
        (work / "d").mkdir()
        assert call("mv", "a", "b")[0] == 0
        assert call("mv", "b", "d")[0] == 0
        assert (work / "d" / "b").read_text() == "x"
        assert not (work / "a").exists()

    def test_missing_source(self, call):
        assert call("mv", "nope", "x")[0] == 1
        assert call("cp", "nope", "x")[0] == 1


class TestWc:
    def test_default_counts(self, call):
        assert call("wc", stdin="a b\nc\n")[1].split() == ["2", "3", "6"]

    def test_flags(self, call):
        assert call("wc", "-l", stdin="a\nb\nc\n")[1].strip() == "3"
        assert call("wc", "-w", stdin="a b c")[1].strip() == "3"
        assert call("wc", "-c", stdin="héllo")[1].strip() == "6"

    def test_files_and_total(self, call, dirs):
        _, work = dirs
        a, b = write(work / "a", "1\n"), write(work / "b", "2\n3\n")
        out = call("wc", "-l", a, b)[1]
        assert out.splitlines()[-1].split() == ["3", "total"]


class TestGrep:
    TEXT = "apple\nBanana\ncherry\napple pie\n"

    def test_match_and_status(self, call):
        status, out, _ = call("grep", "apple", stdin=self.TEXT)
        assert status == 0 and out == "apple\napple pie\n"
        assert call("grep", "zzz", stdin=self.TEXT)[0] == 1

    def test_flags(self, call):
        assert call("grep", "-i", "banana", stdin=self.TEXT)[1] == "Banana\n"
        assert call("grep", "-v", "apple", stdin=self.TEXT)[1] == "Banana\ncherry\n"
        assert call("grep", "-c", "apple", stdin=self.TEXT)[1] == "2\n"
        assert call("grep", "-n", "cherry", stdin=self.TEXT)[1] == "3:cherry\n"
        assert call("grep", "-w", "pie", stdin=self.TEXT)[1] == "apple pie\n"
        assert call("grep", "-F", "a.p", stdin="a.p\nabp\n")[1] == "a.p\n"
        assert call("grep", "-q", "apple", stdin=self.TEXT)[1] == ""
        assert call("grep", "-e", "cherry", stdin=self.TEXT)[1] == "cherry\n"

    def test_regex(self, call):
        assert call("grep", "^a.*ie$", stdin=self.TEXT)[1] == "apple pie\n"

    def test_bad_regex(self, call):
        status, _, err = call("grep", "(", stdin="x")
        assert status == 2 and "invalid regular expression" in err

    def test_several_files_show_names(self, call, dirs):
        _, work = dirs
        a, b = write(work / "a", "hit\n"), write(work / "b", "miss\nhit\n")
        out = call("grep", "hit", a, b)[1]
        assert f"{a}:hit" in out and f"{b}:hit" in out
        assert call("grep", "-l", "miss", a, b)[1] == b + "\n"

    def test_recursive(self, call, dirs):
        _, work = dirs
        (work / "d").mkdir()
        (work / "d" / "x.txt").write_text("needle\n")
        status, out, _ = call("grep", "-r", "needle", "d")
        assert status == 0 and "needle" in out
