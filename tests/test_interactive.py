"""Tests that need a real terminal. They drive the shell through a pseudo terminal
and wait for text to appear, so they do not depend on timing."""
import re
import os
import signal
import sys

import pytest

from conftest import unix_only

pytestmark = [unix_only, pytest.mark.skipif(not hasattr(os, "fork"), reason="needs a pty")]

CH = "#" if hasattr(os, "geteuid") and os.geteuid() == 0 else "$"
# The program prints "re" + "ady" so the word only appears once it is really running,
# never in the echo of the typed command.
PY = f'"{sys.executable}" -c "import time; print(\'re\' + \'ady\', flush=True); time.sleep(60)"'


# Prints one marker when it starts and another after it resumes from a stop.
PY_RESUME = (f'"{sys.executable}" -c "import time; print(\'re\' + \'ady\', flush=True); '
             f'time.sleep(2); print(\'resu\' + \'med\', flush=True); time.sleep(60)"')


def test_prompt_shows_the_directory_in_color(pty_shell):
    s = pty_shell({"TERM": "xterm"})
    s.expect(f"\x1b[1;32m~\x1b[0m {CH} ")


def test_prompt_shows_the_status_of_a_failed_command(pty_shell):
    s = pty_shell({"TERM": "xterm"})
    s.expect(f"{CH} ")
    s.send("false\n")
    s.expect("\x1b[1;31m[1]\x1b[0m")
    s.send("true\n")
    s.expect(f"\x1b[0m {CH} ")
    s.send("echo done\n")
    assert "[1]" not in s.expect("done")


def test_plain_prompt_on_a_dumb_terminal(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("cd /\n")
    s.expect(f"/ {CH} ")


def test_ps1_from_the_rc_file(pty_shell, dirs):
    home, _ = dirs
    (home / ".myshellrc").write_text("PS1='[\\W] > '\nalias zz='echo from-rc'\n")
    s = pty_shell({"TERM": "dumb"})
    s.expect("[~] > ")
    s.send("zz\n")
    s.expect("from-rc")


def test_ctrl_c_stops_the_job_and_keeps_the_shell(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"{CH} ")
    s.send(PY + "\n")
    s.expect("ready")
    s.send("\x03")
    s.expect("[130]")
    s.send("echo alive\n")
    s.expect("alive")


def test_ctrl_z_stops_and_fg_resumes(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"{CH} ")
    s.send(PY_RESUME + "\n")
    s.expect("ready")
    s.send("\x1a")
    s.expect("Stopped")
    s.expect(f"[{128 + signal.SIGTSTP}]")
    s.send("jobs\n")
    s.expect("[1]+  Stopped")
    s.send("fg\n")
    s.expect("resumed")  # the job runs in the foreground again
    s.send("\x03")
    s.expect("[130]")


def test_bg_resumes_a_stopped_job(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"{CH} ")
    s.send(PY + "\n")
    s.expect("ready")
    s.send("\x1a")
    s.expect("Stopped")
    s.send("bg\n")
    s.expect(" &")
    s.send("jobs\n")
    s.expect("Running")
    s.send("echo shell-still-works\n")
    s.expect("shell-still-works")


def test_finished_background_job_is_reported(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"{CH} ")
    s.send("sleep 0.2 &\n")
    s.expect("[1] ")
    s.send("sleep 0.6\n")  # the report appears before the next prompt
    s.expect("Done")


def test_tab_completes_commands_and_paths(pty_shell, dirs):
    _, work = dirs
    (work / "unique_file_name.txt").write_text("CONTENT-OK\n")
    (work / "my file.txt").write_text("SPACE-OK\n")
    s = pty_shell({"TERM": "dumb"}, cwd=work)
    s.expect(f"{CH} ")
    # Each line is finished with Enter, so the output proves what Tab completed.
    s.send('ech\t "a""b"\n')
    s.expect("ab\n")
    s.send("cat unique_f\t\n")
    s.expect("CONTENT-OK")
    s.send("cat my\t\n")
    s.expect("SPACE-OK")


def test_heredoc_asks_for_more_lines_with_ps2(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("cat <<EOF\n")
    s.expect("> ")
    s.send("first line\n")
    s.expect("> ")
    s.send("EOF\n")
    s.expect("first line")
    s.expect(f"~ {CH} ")


def test_ps2_comes_from_the_rc_file(pty_shell, dirs):
    home, _ = dirs
    (home / ".myshellrc").write_text("PS2='more> '\n")
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("cat <<EOF\n")
    s.expect("more> ")
    s.send("EOF\n")
    s.expect(f"~ {CH} ")


def test_ctrl_c_cancels_a_heredoc(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("cat <<EOF\n")
    s.expect("> ")
    s.send("\x03")
    s.expect(f"~ {CH} ")
    s.send("echo alive\n")
    s.expect("alive")


def test_ctrl_d_ends_a_heredoc_with_a_warning(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("cat <<EOF\n")
    s.expect("> ")
    s.send("text\n")
    s.expect("> ")
    s.send("\x04")
    s.expect("wanted `EOF'")
    s.expect(f"~ {CH} ")


def test_history_lists_and_persists(pty_shell, dirs):
    home, _ = dirs
    s = pty_shell({"TERM": "dumb", "MYSHELL_DEBUG": "1"})
    s.expect(f"~ {CH} ")
    s.send("echo alpha\n")
    s.expect(f"~ {CH} ")
    s.send("history\n")
    out = s.expect(f"~ {CH} ")
    assert "    1  echo alpha" in out and "    2  history" in out
    s.send("exit\n")
    import time
    hist = home / ".custom_shell_history"
    for _ in range(80):  # the shell writes the file as it exits
        if hist.exists() and "alpha" in hist.read_text():
            break
        time.sleep(0.1)
    assert hist.exists(), f"no history file. Folder: {os.listdir(home)}. Output: {s.buffer!r}"
    assert re.search(r"echo(\\040| )alpha", hist.read_text())
    s2 = pty_shell({"TERM": "dumb"})
    s2.expect(f"~ {CH} ")
    s2.send("history\n")
    assert "echo alpha" in s2.expect(f"~ {CH} ")


def test_if_block_continues_on_the_next_lines(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("if [ 1 -lt 2 ]; then\n")
    s.expect("> ")
    s.send("echo inside\n")
    s.expect("> ")
    s.send("fi\n")
    s.expect("inside")
    s.expect(f"~ {CH} ")


def test_for_loop_continues_on_the_next_lines(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("for x in 1 2; do\n")
    s.expect("> ")
    s.send("echo n$x\n")
    s.expect("> ")
    s.send("done\n")
    s.expect("n1")
    s.expect("n2")
    s.expect(f"~ {CH} ")


def test_ctrl_c_stops_a_running_loop(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("while true; do sleep 0.1; done\n")
    import time
    time.sleep(0.5)
    s.send("\x03")
    s.expect(f"~ {CH} ")
    s.send("echo alive\n")
    s.expect("alive")


def test_pip_greets_on_start(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    out = s.expect("Tip:")
    assert "I am Pip" in out
    s.expect(f"~ {CH} ")


def test_greeting_can_be_turned_off_in_the_rc_file(pty_shell, dirs):
    home, _ = dirs
    (home / ".myshellrc").write_text("GUIDE=off\n")
    s = pty_shell({"TERM": "dumb"})
    out = s.expect(f"~ {CH} ")
    assert "Pip" not in out
    s.send("ecoh hi\n")
    out = s.expect(f"~ {CH} ")
    assert "Did you mean" not in out


def test_pip_suggests_a_fix_for_a_typo(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("ecoh hi\n")
    s.expect("ecoh: command not found")
    out = s.expect("?")
    assert "'echo'" in out
    s.expect(f"~ {CH} ")


def test_pip_explains_an_unclosed_quote(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("echo 'oops\n")
    s.expect("A quote was opened and never closed")
    s.expect(f"~ {CH} ")


def test_pip_stays_quiet_when_a_program_exists_but_fails(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("sh -c 'exit 127'\n")
    s.send("echo marker\n")
    out = s.expect("marker")
    assert "Did you mean" not in out and "could not find" not in out


def test_guide_menu_runs_a_lesson_in_the_terminal(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("guide\n")
    s.expect("Pick one > ")
    s.send("1\n")
    s.expect("Enter runs it. s skips. q stops > ")
    s.send("\n")
    s.expect("hello from myshell")
    s.expect("q stops > ")
    s.send("q\n")
    s.expect("Type guide when you want to continue")
    s.expect(f"~ {CH} ")


def test_lesson_answers_stay_out_of_history(pty_shell):
    s = pty_shell({"TERM": "dumb"})
    s.expect(f"~ {CH} ")
    s.send("guide basics\n")
    s.expect("q stops > ")
    s.send("q\n")
    s.expect(f"~ {CH} ")
    s.send("history\n")
    out = s.expect(f"~ {CH} ")
    assert "guide basics" in out
    assert not re.search(r"\d+  q\b", out)
