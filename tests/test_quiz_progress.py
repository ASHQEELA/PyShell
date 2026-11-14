"""Pip's quiz, progress record and the new lessons."""
import io
import json
import random

import pytest

from myshell import guide, progress, quiz


def scripted(answers):
    """An ask function that reads from a list, then reports end of input."""
    items = list(answers)

    def ask(prompt):
        return items.pop(0) if items else None
    return ask


def correct_letter(item):
    return "abcd"[item.answer]


class TestQuestionBank:
    def test_every_topic_has_a_lesson(self):
        keys = {x.key for x in guide.build_lessons()}
        assert set(quiz.topics()) <= keys

    def test_every_lesson_has_questions(self):
        assert {x.key for x in guide.build_lessons()} <= set(quiz.topics())

    def test_questions_are_well_formed(self):
        for item in quiz.QUESTIONS:
            assert 2 <= len(item.choices) <= 4
            assert 0 <= item.answer < len(item.choices)
            assert len(set(item.choices)) == len(item.choices)
            assert item.why

    def test_pick_is_seeded_and_shuffles_choices(self):
        a = quiz.pick(5, rng=random.Random(1))
        b = quiz.pick(5, rng=random.Random(1))
        assert [x.text for x in a] == [x.text for x in b]
        for item in a:
            original = next(x for x in quiz.QUESTIONS if x.text == item.text)
            assert item.choices[item.answer] == original.choices[original.answer]

    def test_pick_topic_and_limits(self):
        assert {x.topic for x in quiz.pick(50, "case")} == {"case"}
        assert len(quiz.pick(50, "case")) == len([x for x in quiz.QUESTIONS if x.topic == "case"])


class TestProgressFile:
    def test_missing_and_damaged_files_start_fresh(self, tmp_path):
        assert progress.load(str(tmp_path / "none")) == progress.empty()
        bad = tmp_path / "bad"
        bad.write_text("{not json")
        assert progress.load(str(bad)) == progress.empty()
        odd = tmp_path / "odd"
        odd.write_text(json.dumps([1, 2]))
        assert progress.load(str(odd)) == progress.empty()

    def test_marks_lessons(self, tmp_path):
        path = str(tmp_path / "p.json")
        progress.mark_lesson(path, "basics")
        progress.mark_lesson(path, "basics")
        assert progress.done_lessons(path) == ["basics"]
        assert progress.load(path)["lessons"]["basics"]["times"] == 2

    def test_quiz_best_score(self, tmp_path):
        path = str(tmp_path / "p.json")
        assert progress.record_quiz(path, "all", 3, 5) is True
        assert progress.record_quiz(path, "all", 2, 5) is False
        assert progress.record_quiz(path, "all", 4, 5) is True
        assert progress.best_scores(path) == {"all": (4, 5)}

    def test_keeps_only_recent_runs(self, tmp_path):
        path = str(tmp_path / "p.json")
        for _ in range(progress.KEEP_RUNS + 5):
            progress.record_quiz(path, "all", 1, 5)
        assert len(progress.load(path)["quizzes"]["runs"]) == progress.KEEP_RUNS

    def test_unwritable_location_does_not_raise(self, tmp_path):
        assert progress.save(str(tmp_path / "no" / "such" / "p.json"), progress.empty()) is False

    def test_reset(self, tmp_path):
        path = str(tmp_path / "p.json")
        progress.mark_lesson(path, "basics")
        progress.reset(path)
        progress.reset(path)  # a second reset is fine
        assert progress.done_lessons(path) == []


class TestRunQuiz:
    def test_perfect_score_is_saved(self, state):
        out = io.StringIO()
        rng = random.Random(3)
        expected = quiz.pick(3, rng=random.Random(3))
        answers = [correct_letter(x) for x in expected]
        assert guide.run_quiz(state, out, scripted(answers), count=3, rng=rng) == 0
        assert "You got 3 of 3" in out.getvalue()
        assert progress.best_scores(state.progress_file)["all"] == (3, 3)

    def test_wrong_answer_explains(self, state):
        out = io.StringIO()
        item = quiz.pick(1, rng=random.Random(5))[0]
        wrong = "abcd"[(item.answer + 1) % len(item.choices)]
        guide.run_quiz(state, out, scripted([wrong]), count=1, rng=random.Random(5))
        flat = " ".join(out.getvalue().replace("|", " ").split())  # the speech bubble wraps lines
        assert "Not quite" in flat
        assert "You got 0 of 1" in flat

    def test_number_answers_work_and_bad_input_asks_again(self, state):
        out = io.StringIO()
        item = quiz.pick(1, rng=random.Random(5))[0]
        guide.run_quiz(state, out, scripted(["zzz", str(item.answer + 1)]),
                       count=1, rng=random.Random(5))
        assert "Type a, b, c or d" in out.getvalue() and "You got 1 of 1" in out.getvalue()

    def test_stopping_saves_nothing(self, state):
        out = io.StringIO()
        guide.run_quiz(state, out, scripted(["q"]), count=3)
        assert progress.best_scores(state.progress_file) == {}

    def test_end_of_input_stops(self, state):
        out = io.StringIO()
        assert guide.run_quiz(state, out, scripted([]), count=3) == 0
        assert progress.best_scores(state.progress_file) == {}

    def test_topic_quiz_uses_topic_name(self, state):
        out = io.StringIO()
        items = quiz.pick(2, "case", rng=random.Random(9))
        guide.run_quiz(state, out, scripted([correct_letter(x) for x in items]),
                       topic="case", count=2, rng=random.Random(9))
        assert "case" in progress.best_scores(state.progress_file)

    def test_unknown_topic_has_no_questions(self, state):
        out = io.StringIO()
        assert guide.run_quiz(state, out, scripted([]), topic="nope") == 1


class TestGuideCommands:
    def test_progress_when_empty(self, sh):
        r = sh("guide progress")
        assert "Lessons finished: 0 of" in r.out and "No quiz taken yet" in r.out

    def test_progress_reset(self, sh, dirs):
        home, _ = dirs
        (home / ".myshell_progress.json").write_text(json.dumps(
            {"lessons": {"basics": {"times": 1}}}))
        assert "[x] First steps" in sh("guide progress").out
        assert "cleared" in sh("guide progress reset").out
        assert "Lessons finished: 0" in sh("guide progress").out

    def test_quiz_argument_errors(self, sh):
        r = sh("guide quiz nonsense")
        assert r.code == 1 and "unknown quiz topic" in r.err

    def test_quiz_with_no_input_ends_cleanly(self, sh):
        r = sh("guide quiz case 2")
        assert r.code == 0 and "2 questions about case" in " ".join(r.out.replace("|", " ").split())

    def test_quiz_topic_prefix_and_count(self):
        assert guide.parse_quiz_args(["fun", "3"]) == ("functions", 3)
        assert guide.parse_quiz_args(["99"]) == (None, 20)
        assert isinstance(guide.parse_quiz_args(["zz"]), str)

    def test_menu_shows_marks_and_new_entries(self, sh, dirs):
        home, _ = dirs
        (home / ".myshell_progress.json").write_text(json.dumps(
            {"lessons": {"case": {"times": 1}}}))
        out = sh("guide list").out
        assert "Case statements" in out and "[x]" in out and "z) Quiz" in out


class TestLessons:
    def test_finishing_a_lesson_records_it(self, state):
        lesson = next(x for x in guide.build_lessons() if x.key == "case")
        out = io.StringIO()
        assert guide.run_lesson(state, lesson, out, lambda prompt: "") is True
        assert progress.done_lessons(state.progress_file) == ["case"]

    def test_quitting_a_lesson_records_nothing(self, state):
        lesson = next(x for x in guide.build_lessons() if x.key == "case")
        guide.run_lesson(state, lesson, io.StringIO(), lambda prompt: "q")
        assert progress.done_lessons(state.progress_file) == []

    @pytest.mark.parametrize("key", [x.key for x in guide.build_lessons()])
    def test_every_lesson_runs_without_errors(self, state, key, capfd, monkeypatch):
        """Each command in each lesson must work. The commands run in the shell for real."""
        monkeypatch.setenv("MYSHELL_PORTABLE", "1")
        state.keep_going = True
        lesson = next(x for x in guide.build_lessons() if x.key == key)
        out = io.StringIO()
        assert guide.run_lesson(state, lesson, out, lambda prompt: "") is True
        err = capfd.readouterr().err
        assert err == "", f"lesson {key} printed errors: {err}"

    def test_lessons_leave_no_names_behind(self, state, monkeypatch):
        monkeypatch.setenv("MYSHELL_PORTABLE", "1")
        for lesson in guide.build_lessons():
            guide.run_lesson(state, lesson, io.StringIO(), lambda prompt: "")
        assert state.functions == {} and state.traps == {} and "errexit" not in state.options
