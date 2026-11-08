import signal

import pytest

from myshell.errors import ShellError
from myshell.jobs import Job, JobControl


def job(statuses, command="sleep 1", **kw):
    pids = list(statuses)
    return Job(pgid=pids[0], pids=pids, command=command, status=dict(statuses), **kw)


class TestJob:
    def test_running_until_every_process_has_a_status(self):
        j = job({10: 0, 11: None})
        assert not j.completed
        assert j.state_text() == "Running"

    def test_completed(self):
        j = job({10: 0, 11: 0})
        assert j.completed
        assert j.state_text() == "Done"

    def test_status_is_the_last_process(self):
        assert job({10: 1, 11: 0}).last_status == 0
        assert job({10: 0, 11: 3}).last_status == 3

    def test_nonzero_exit_text(self):
        assert job({10: 3}).state_text() == "Exit 3"

    def test_killed_by_a_signal(self):
        j = job({10: 128 + signal.SIGTERM}, term_signal=signal.SIGTERM)
        assert j.state_text() == signal.strsignal(signal.SIGTERM)

    def test_stopped_needs_every_live_process_stopped(self):
        j = job({10: None, 11: None})
        j.stopped_pids = {10}
        assert not j.stopped
        j.stopped_pids = {10, 11}
        assert j.stopped
        assert j.state_text() == "Stopped"

    def test_finished_processes_do_not_block_a_stop(self):
        j = job({10: 0, 11: None})
        j.stopped_pids = {11}
        assert j.stopped

    def test_a_completed_job_is_never_stopped(self):
        j = job({10: 0})
        j.stopped_pids = {10}
        assert not j.stopped


class TestJobControl:
    @pytest.fixture
    def jc(self):
        return JobControl()

    def numbered(self, jc, count):
        jobs = []
        for i in range(count):
            j = jc.add(100 + i, [100 + i], f"cmd{i}", background=True)
            jc.assign_number(j)
            jobs.append(j)
        return jobs

    def test_numbers_count_up(self, jc):
        assert [j.number for j in self.numbered(jc, 3)] == [1, 2, 3]

    def test_a_number_is_assigned_once(self, jc):
        (j,) = self.numbered(jc, 1)
        jc.assign_number(j)
        assert j.number == 1

    def test_numbers_are_reused_after_removal(self, jc):
        a, b = self.numbered(jc, 2)
        jc.remove(b)
        c = jc.add(200, [200], "new", background=True)
        jc.assign_number(c)
        assert c.number == 2

    def test_current_and_previous_markers(self, jc):
        a, b, c = self.numbered(jc, 3)
        assert [jc.marker(j) for j in (a, b, c)] == [" ", "-", "+"]

    def test_find_by_spec(self, jc):
        a, b = self.numbered(jc, 2)
        assert jc.find(None) is b
        assert jc.find("%+") is b
        assert jc.find("%%") is b
        assert jc.find("%-") is a
        assert jc.find("%1") is a
        assert jc.find("2") is b

    @pytest.mark.parametrize("spec", ["%9", "%x", "abc"])
    def test_unknown_spec(self, jc, spec):
        self.numbered(jc, 1)
        with pytest.raises(ShellError, match="no such job"):
            jc.find(spec)

    def test_no_jobs(self, jc):
        with pytest.raises(ShellError, match="no current job"):
            jc.find(None)

    def test_no_previous_job(self, jc):
        self.numbered(jc, 1)
        with pytest.raises(ShellError, match="no previous job"):
            jc.find("%-")

    def test_format_line(self, jc):
        (j,) = self.numbered(jc, 1)
        j.status = {100: None}
        text = jc.format(j)
        assert text.startswith("[1]+  Running")
        assert text.endswith("cmd0 &")
