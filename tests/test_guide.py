import os
import subprocess
import sys

import pytest

from conftest import clean_env, unix_only
from myshell import guide
from myshell.errors import ShellSyntaxError
from myshell.lexer import lex
from myshell.parser import parse


class TestCharacter:
    @pytest.mark.parametrize("mood", list(guide.MOODS))
    def test_every_mood_has_four_equal_rows(self, mood):
        rows = guide.pip_lines(mood)
        assert len(rows) == 4 and {len(r) for r in rows} == {guide.ART_WIDTH}

    def test_moods_change_the_face(self):
        faces = {m: guide.pip_lines(m)[1] for m in guide.MOODS}
        assert len(set(faces.values())) == len(faces)
        assert "^ ^" in faces["happy"] and "' '" in faces["worried"]

    def test_unknown_mood_falls_back_to_happy(self):
        assert guide.pip_lines("zzz") == guide.pip_lines("happy")

    def test_bubble_wraps_and_boxes(self):
        box = guide.bubble("word " * 30, 20)
        assert len({len(line) for line in box}) == 1
        assert box[0].startswith(".") and box[-1].startswith("'")
        assert all(len(line) <= 24 for line in box)

    def test_bubble_keeps_blank_lines(self):
        assert len(guide.bubble("a\n\nb", 20)) == 5

    def test_render_puts_pip_beside_the_text(self):
        text = guide.render("Hello there", "happy")
        assert "Hello there" in text and "( ^ ^ )" in text
        assert len(text.splitlines()) >= 4

    def test_render_adds_color_only_when_asked(self):
        assert "\x1b[" not in guide.render("x", color=False)
        assert guide.YELLOW in guide.render("x", color=True)

    def test_narrow_terminal_still_renders(self):
        assert "hi" in guide.render("hi", columns=10)

    def test_say_prints_to_the_stream(self, capsys):
        guide.say("Hello", "wow")
        out = capsys.readouterr().out
        assert "Hello" in out and "* *" in out

    def test_lines_do_not_end_with_spaces(self):
        assert all(line == line.rstrip() for line in guide.render("short").splitlines())


class TestLessons:
    def test_keys_are_unique_and_every_step_parses(self):
        lessons = guide.build_lessons()
        assert len({l.key for l in lessons}) == len(lessons)
        for lesson in lessons:
            for step in lesson.steps:
                if step.command and "<<EOF" not in step.command:
                    parse(lex(step.command))

    def test_find_by_number_name_and_prefix(self):
        lessons = guide.build_lessons()
        assert guide.find_lesson(lessons, "1").key == "basics"
        assert guide.find_lesson(lessons, "PIPES").key == "pipes"
        assert guide.find_lesson(lessons, "var").key == "variables"
        assert guide.find_lesson(lessons, "0") is None
        assert guide.find_lesson(lessons, "99") is None
        assert guide.find_lesson(lessons, "zz") is None
        assert guide.find_lesson(lessons, "b") is None  # one letter is too vague

    @unix_only
    def test_running_a_lesson_runs_the_examples(self, state, capfd):
        answers = iter([""] * 20)
        lesson = guide.find_lesson(guide.build_lessons(), "variables")
        assert guide.run_lesson(state, lesson, sys.stdout, lambda p: next(answers)) is True
        out = capfd.readouterr().out
        assert "hello world" in out and "42" in out and "n is 6" in out

    @unix_only
    def test_skip_does_not_run(self, state, capfd):
        answers = iter(["s"] * 20)
        lesson = guide.find_lesson(guide.build_lessons(), "variables")
        guide.run_lesson(state, lesson, sys.stdout, lambda p: next(answers))
        assert "hello world" not in capfd.readouterr().out

    def test_quit_stops_the_lesson(self, state, capfd):
        lesson = guide.find_lesson(guide.build_lessons(), "variables")
        assert guide.run_lesson(state, lesson, sys.stdout, lambda p: "q") is False
        assert "42" not in capfd.readouterr().out

    def test_end_of_input_stops_the_lesson(self, state):
        lesson = guide.find_lesson(guide.build_lessons(), "basics")
        assert guide.run_lesson(state, lesson, sys.stdout, lambda p: None) is False

    @unix_only
    def test_status_is_restored_after_examples(self, state):
        state.last_status = 7
        lesson = guide.find_lesson(guide.build_lessons(), "variables")
        guide.run_lesson(state, lesson, sys.stdout, lambda p: "")
        assert state.last_status == 7

    @unix_only
    def test_examples_leave_no_variables_behind(self, state):
        lesson = guide.find_lesson(guide.build_lessons(), "variables")
        guide.run_lesson(state, lesson, sys.stdout, lambda p: "")
        assert state.get_variable("demo") is None and state.get_variable("n") is None

    @unix_only
    def test_demo_file_is_removed(self, state):
        import tempfile
        lesson = guide.find_lesson(guide.build_lessons(), "pipes")
        guide.run_lesson(state, lesson, sys.stdout, lambda p: "")
        assert not os.path.exists(f"{tempfile.gettempdir()}/pip_demo.txt")

    def test_shown_only_steps_are_not_run(self, state, capfd):
        lesson = guide.find_lesson(guide.build_lessons(), "customize")
        asked = []
        guide.run_lesson(state, lesson, sys.stdout, lambda p: asked.append(p) or "s")
        assert len(asked) == 1  # only the history step asks
        assert "ll" not in state.aliases


class TestMenu:
    def test_menu_runs_a_topic_then_quits(self, state, capfd):
        answers = iter(["nonsense", "c", "quotes", "s", "s", "s", "s", "q"])
        assert guide.run_menu(state, sys.stdout, lambda p: next(answers)) == 0
        out = capfd.readouterr().out
        assert "I do not know that one" in out
        assert "Run and combine" in out and "Quotes and wildcards" in out

    def test_menu_ends_on_end_of_input(self, state, capfd):
        assert guide.run_menu(state, sys.stdout, lambda p: None) == 0
        assert "See you soon" in capfd.readouterr().out


class TestHints:
    def test_close_match_is_suggested(self, state):
        assert "Did you mean 'echo'" in guide.missing_hint("ecoh", state)

    def test_alias_names_are_considered(self, state):
        state.aliases["deploy"] = "echo x"
        assert "'deploy'" in guide.missing_hint("deplyo", state)

    def test_no_close_match(self, state):
        hint = guide.missing_hint("qqqqzzzzxxxx", state)
        assert "could not find" in hint and "Did you mean" not in hint

    def test_existing_program_gets_no_hint(self, state):
        assert guide.missing_hint("sh", state) is None

    def test_a_path_gets_no_hint(self, state):
        assert guide.missing_hint("./nothing", state) is None

    def test_several_close_matches(self, state):
        state.aliases.update({"gitx": "a", "gity": "b"})
        hint = guide.missing_hint("gitz", state)
        assert "'gitx'" in hint and "'gity'" in hint

    @pytest.mark.parametrize("line, fragment", [
        ("echo 'x", "quote was opened"),
        ("echo $(ls", "never closed"),
        ("echo `ls", "never closed"),
        ("a &&", "Something must follow"),
        ("a | | b", "need a command"),
    ])
    def test_syntax_hints(self, line, fragment):
        with pytest.raises(ShellSyntaxError) as info:
            parse(lex(line))
        assert fragment in guide.syntax_hint(str(info.value))

    def test_unknown_message_has_no_hint(self):
        assert guide.syntax_hint("something else") is None

    def test_guide_switch(self, state):
        assert guide.guide_enabled(state)
        state.set_variable("GUIDE", "OFF")
        assert not guide.guide_enabled(state)

    def test_tips_are_plain_sentences(self):
        assert all(t.endswith(".") and len(t) < 90 for t in guide.TIPS)
        assert guide.tip() in guide.TIPS


@unix_only
class TestBuiltin:
    def test_list(self, sh):
        r = sh("guide list")
        assert "1) First steps" in r.out and "q) Quit" in r.out

    def test_cheat(self, sh):
        assert "Run and combine" in sh("guide cheat").out

    def test_tip_shows_pip(self, sh):
        assert "( ^ ^ )" in sh("guide tip").out

    def test_bare_guide_exits_cleanly_without_input(self, sh):
        r = sh("guide")
        assert r.code == 0 and "Hi, I am Pip" in r.out and "See you soon" in r.out

    def test_unknown_topic(self, sh):
        r = sh("guide nope; echo $?")
        assert "unknown topic 'nope'" in r.err and r.out == "1\n"

    def test_too_many_arguments(self, sh):
        assert "too many arguments" in sh("guide a b").err

    def test_off_and_on_set_the_variable(self, sh):
        r = sh("guide off; echo $GUIDE; guide on; echo $GUIDE")
        assert "off\n" in r.out and "on\n" in r.out

    def test_topic_by_name_reads_answers_from_stdin(self, dirs):
        _, work = dirs
        home, _ = dirs
        p = subprocess.run([sys.executable, "-m", "myshell", "-c", "guide variables"],
                           input="\n\n\n\n\n", cwd=work, env=clean_env(home),
                           capture_output=True, text=True, timeout=30)
        assert "hello world" in p.stdout and "42" in p.stdout

    def test_type_knows_it(self, sh):
        assert "builtin" in sh("type guide").out


@unix_only
class TestNoNoiseOutsideATerminal:
    def test_piped_session_has_no_greeting_or_hints(self, dirs):
        home, work = dirs
        p = subprocess.run([sys.executable, "-m", "myshell"], input="ecoh hi\necho ok\n",
                           cwd=work, env=clean_env(home), capture_output=True, text=True, timeout=30)
        assert "Pip" not in p.stdout + p.stderr and "Did you mean" not in p.stdout + p.stderr
        assert "ok" in p.stdout

    def test_scripts_have_no_hints(self, sh):
        assert "Did you mean" not in sh("ecoh hi").err


class TestColorChoice:
    class Fake:
        def __init__(self, tty):
            self.tty = tty

        def isatty(self):
            return self.tty

    def test_plain_stream_has_no_color(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm")
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert guide.use_color(self.Fake(False)) is False
        assert guide.use_color(__import__("io").StringIO()) is False

    def test_terminal_gets_color(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm")
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert guide.use_color(self.Fake(True)) is True

    def test_dumb_terminal_and_no_color_turn_it_off(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        assert guide.use_color(self.Fake(True)) is False
        monkeypatch.setenv("TERM", "xterm")
        monkeypatch.setenv("NO_COLOR", "1")
        assert guide.use_color(self.Fake(True)) is False


@unix_only
class TestMissingCommandRecord:
    def test_lone_missing_command_is_recorded(self, state, capfd):
        state.execute("nosuchprog1")
        assert state.missing_command == "nosuchprog1"

    def test_pipeline_does_not_record(self, state, capfd):
        state.execute("nosuchprog1 | nosuchprog2")
        assert state.missing_command is None

    def test_found_command_does_not_record(self, state, capfd):
        state.execute("true")
        assert state.missing_command is None
