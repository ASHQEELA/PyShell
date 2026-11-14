"""Pip, the shell guide: a small character that greets you, teaches by example,
and explains mistakes. Pip is an original character made for this shell.

    guide              open the topic menu
    guide TOPIC        run one lesson (a name or a number)
    guide list         list the topics
    guide cheat        print a cheat sheet
    guide quiz [TOPIC] [N]   test yourself with N questions (default 5)
    guide progress     show finished lessons and quiz scores  (reset clears them)
    guide tip          print a random tip
    guide off | on     hide or show Pip's greeting and hints

Set GUIDE=off in ~/.myshellrc to start without Pip."""
import difflib
import os
import random
import shutil
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, TextIO

from . import compat, history, progress, quiz
from .state import ShellState

# --- the character ---------------------------------------------------------

ART = [
    r" ,---. ",
    r"( {e} )",
    r" \ {m} / ",
    r" (_@_)>",
]
MOODS = {  # mood: (eyes, mouth)
    "happy": ("^ ^", "v"),
    "think": ("o o", "-"),
    "worried": ("' '", "~"),
    "wow": ("* *", "O"),
}
ART_WIDTH = 7
YELLOW, RESET = "\x1b[33m", "\x1b[0m"


def pip_lines(mood: str = "happy") -> List[str]:
    eyes, mouth = MOODS.get(mood, MOODS["happy"])
    return [line.format(e=eyes, m=mouth).ljust(ART_WIDTH)[:ART_WIDTH] for line in ART]


def bubble(text: str, width: int) -> List[str]:
    """Wrap text and draw a box around it."""
    wrapped: List[str] = []
    for paragraph in text.split("\n"):
        wrapped.extend(textwrap.wrap(paragraph, width) or [""])
    inner = max(len(line) for line in wrapped)
    lines = ["." + "-" * (inner + 2) + "."]
    lines += ["| " + line.ljust(inner) + " |" for line in wrapped]
    lines.append("'" + "-" * (inner + 2) + "'")
    return lines


def use_color(out: TextIO) -> bool:
    return (hasattr(out, "isatty") and out.isatty() and "NO_COLOR" not in os.environ
            and os.environ.get("TERM", "") not in ("", "dumb"))


def render(text: str, mood: str = "happy", color: bool = False, columns: int = 80) -> str:
    """Pip on the left and a speech bubble on the right."""
    width = max(20, min(columns - ART_WIDTH - 8, 60))
    art = pip_lines(mood)
    box = bubble(text, width)
    rows = max(len(art), len(box))
    out = []
    for i in range(rows):
        face = art[i] if i < len(art) else " " * ART_WIDTH
        if color and i < len(art):
            face = YELLOW + face + RESET
        out.append(f"{face}  {box[i] if i < len(box) else ''}".rstrip())
    return "\n".join(out)


def say(text: str, mood: str = "happy", out: Optional[TextIO] = None) -> None:
    out = out or sys.stdout
    columns = shutil.get_terminal_size((80, 24)).columns
    print(render(text, mood, use_color(out), columns), file=out)


# --- lessons ---------------------------------------------------------------

@dataclass
class Step:
    text: str
    command: str = ""
    run: bool = True  # False shows the command without running it


@dataclass
class Lesson:
    key: str
    title: str
    summary: str
    steps: List[Step] = field(default_factory=list)


def build_lessons() -> List[Lesson]:
    tmp = Path(tempfile.gettempdir()).as_posix()  # forward slashes, so Windows paths work too
    demo = f"{tmp}/pip_demo.txt"
    wild = f"{tmp}/pip_wild"
    background = ([Step("Add & to run a command in the background. jobs lists them. wait waits.",
                        "sleep 1 & jobs; wait")] if compat.HAS_FORK else
                  [Step("On this system commands do not run in the background. On Linux and macOS, "
                        "a trailing & starts one, jobs lists them and wait waits.")])
    return [
        Lesson("basics", "First steps", "run commands and move around", [
            Step("A shell runs commands. echo prints text.", "echo hello from myshell"),
            Step("pwd shows the folder you are in.", "pwd"),
            Step("cd moves you to another folder. cd - jumps back.", "cd / && pwd && cd -"),
            Step("Tab completes commands and file names. Press it twice to see choices. "
                 "Press the up arrow to bring back an earlier command."),
        ]),
        Lesson("pipes", "Pipes and files", "connect commands and save output", [
            Step("A pipe sends the output of one command into the next.",
                 "echo hello | tr a-z A-Z"),
            Step("> writes output to a file. >> adds to the end of a file.",
                 f"echo first > {demo}; echo second >> {demo}; cat {demo}; rm {demo}"),
            Step("2>&1 sends error messages down the same path as normal output.",
                 "ls /no/such/folder 2>&1 | head -1"),
            Step("A here-string feeds a line of text to a command as input.",
                 "cat <<< 'from a here-string'"),
            Step("A here-document feeds several lines. It ends at the marker word.",
                 "cat <<EOF\nline one\nline two\nEOF", run=False),
        ]),
        Lesson("variables", "Variables and math", "store values and calculate", [
            Step("Set a variable with NAME=value. Read it with $NAME.",
                 "demo=world; echo hello $demo; unset demo"),
            Step("Put math inside $(( )). It handles + - * / % and more.",
                 "echo $((6 * 7))"),
            Step("Variables work inside math too, with += and similar.",
                 "n=5; n=$((n + 1)); echo n is $n; unset n"),
            Step("$( ) captures the output of a command. Backticks do the same.",
                 "echo I am in $(pwd)"),
            Step("$? holds the exit status of the last command. 0 means success.",
                 "true; echo $?; false; echo $?"),
        ]),
        Lesson("quotes", "Quotes and wildcards", "control what the shell changes", [
            Step("Single quotes keep text exactly as typed.",
                 "echo '$HOME stays as typed'"),
            Step("Double quotes allow variables and keep spaces together.",
                 'echo "your home is $HOME"'),
            Step("A star matches any run of characters in file names. This makes two demo files "
                 "and matches them.",
                 f"touch {wild}_a.demo {wild}_b.demo; echo {wild}_*.demo; rm {wild}_*.demo"),
            Step("A backslash turns off the next character's special meaning.",
                 r"echo \$HOME costs \* nothing"),
        ]),
        Lesson("logic", "Choices and loops", "decide and repeat", [
            Step("&& runs the next command only after success. || runs it after failure.",
                 "true && echo yes; false || echo fallback"),
            Step("if tests a condition. [ ] compares numbers, text and files.",
                 "if [ 3 -gt 2 ]; then echo bigger; else echo smaller; fi"),
            Step("for repeats a command for each item in a list.",
                 "for n in 1 2 3; do echo number $n; done"),
            Step("while repeats as long as a test passes. break leaves a loop early.",
                 "i=0; while [ $i -lt 5 ]; do i=$((i+1)); [ $i = 3 ] && break; echo i is $i; done; unset i"),
            Step("File tests: -f is a file, -d is a folder, -e exists.",
                 "[ -d . ] && echo . is a folder"),
        ]),
        Lesson("jobs", "Background jobs", "run several things at once", [
            *background,
            Step("Press Ctrl+C to stop a running command. Press Ctrl+Z to pause it. "
                 "Then bg runs it in the background, and fg brings it back."),
        ]),
        Lesson("functions", "Functions and arguments", "name a few commands and reuse them", [
            Step("A function groups commands under a name. Call it like any command.",
                 "pip_hi() { echo hello $1; }; pip_hi Pip"),
            Step("$1, $2 ... are the arguments. $# counts them. $@ lists them all.",
                 'pip_args() { echo "$# arguments: $@"; }; pip_args one "two words" three'),
            Step('Use "$@" in quotes to pass every argument on, with spaces kept.',
                 'pip_each() { for item in "$@"; do echo "[$item]"; done; }; pip_each "a b" c'),
            Step("return ends the function with a status. $? shows it.",
                 "pip_check() { [ $1 -gt 5 ] && return 0; return 1; }; pip_check 9; echo $?"),
            Step("local keeps a variable inside the function.",
                 "x=outside; pip_local() { local x=inside; echo $x; }; pip_local; echo $x; unset x"),
            Step("A for loop with no list walks over the arguments. Scripts get arguments too: "
                 "myshell script.sh a b makes $1 equal to a.",
                 "pip_walk() { for a; do echo item $a; done; }; pip_walk x y; "
                 "unset -f pip_hi pip_args pip_each pip_check pip_local pip_walk"),
        ]),
        Lesson("expansion", "Expansions", "defaults, trimming, replacing and braces", [
            Step("${NAME:-default} uses a default when NAME is empty or unset.",
                 "echo ${pip_none:-fallback}"),
            Step("${#NAME} is the length of the value.",
                 "pip_w=shell; echo ${#pip_w}"),
            Step("${NAME%pattern} cuts a matching ending. ${NAME#pattern} cuts a matching start. "
                 "Doubling the symbol cuts the longest match.",
                 "pip_f=report.final.txt; echo ${pip_f%.txt} ${pip_f%%.*} ${pip_f#*.} ${pip_f##*.}"),
            Step("${NAME/old/new} replaces the first match. Two slashes replace all.",
                 "echo ${pip_f/./_} ${pip_f//./_}"),
            Step("${NAME:start:length} cuts a slice. ${NAME^^} makes capitals.",
                 "echo ${pip_f:0:6} ${pip_f^^}; unset pip_f pip_w"),
            Step("{a,b} builds several words. {1..5} counts.",
                 "echo file.{txt,md}; echo {1..5}; echo {a..e}; echo {01..03}"),
            Step("Braces save typing when you make files or folders.",
                 f"mkdir -p {tmp}/pip_b/{{one,two}}; ls {tmp}/pip_b; rm -r {tmp}/pip_b"),
        ]),
        Lesson("case", "Case statements", "match one value against patterns", [
            Step("case compares a word with patterns. Each arm ends with ;;.",
                 "case apple in apple) echo fruit;; carrot) echo vegetable;; esac"),
            Step("Use | for several patterns and * as the catch-all.",
                 "case notes.md in *.txt|*.md) echo text;; *.png) echo image;; *) echo other;; esac"),
            Step("Patterns can use ? and [ ] too.",
                 "case 7 in [0-9]) echo one digit;; *) echo longer;; esac"),
            Step("Inside a function, case makes a tidy menu.",
                 "pip_do() { case $1 in start) echo starting;; stop) echo stopping;; "
                 "*) echo unknown $1;; esac; }; pip_do start; pip_do jump; unset -f pip_do"),
        ]),
        Lesson("tools", "More builtins", "read, printf, set, env, which, trap and friends", [
            Step("read puts a line of input into variables. Here it reads a here-string.",
                 'read pip_a pip_b <<< "first second third"; echo $pip_a / $pip_b; unset pip_a pip_b'),
            Step("printf formats text. %s is text, %d is a whole number, %5.2f is a decimal.",
                 "printf '%s has %d items costing %5.2f\\n' cart 3 9.5"),
            Step("printf repeats its format for extra arguments.",
                 "printf '%s\\n' one two three"),
            Step("env lists the environment. env NAME=value command runs a command with that value.",
                 "env PIP_COLOR=blue echo run with a temporary value"),
            Step("which tells how a name runs: builtin, alias, function or program path.",
                 "alias pip_a='echo hi'; which echo pip_a; unalias pip_a"),
            Step("trap runs a command when a signal or the shell exit arrives.",
                 "trap 'echo goodbye from trap' EXIT; trap; trap - EXIT"),
            Step("set -e stops a script at the first failure. set -u stops at an unset variable. "
                 "set -x prints each command. Turn one off with +e, +u or +x. "
                 "set -- a b c sets $1 $2 $3."),
            Step("exec replaces the shell with a command. true, false and : do nothing "
                 "and give status 0 or 1."),
        ]),
        Lesson("prompt", "Prompt extras", "git branch, the time and slow commands", [
            Step("Inside a git repository the default prompt shows the branch in brackets. "
                 "Use \\g in your own PS1 to place it."),
            Step("\\t is the time as 24-hour HH:MM:SS. \\A is HH:MM. \\d is the date.",
                 "PS1='\\t \\w \\$ '", run=False),
            Step("The shell prints how long a command took when it ran longer than 2 seconds. "
                 "Change the limit with SLOW_COMMAND_SECS, or set it to off.",
                 "SLOW_COMMAND_SECS=5", run=False),
        ]),
        Lesson("typing", "Typing faster", "history search, suggestions and colors", [
            Step("Press Ctrl+R and type part of an old command. Press Ctrl+R again for the "
                 "next match. Enter runs it. Esc lets you edit it first."),
            Step("As you type, a grey suggestion from your history appears. "
                 "Press the Right arrow or End to accept it."),
            Step("Colors show what you typed. A known command is green, an unknown one is red, "
                 "strings are yellow and variables are cyan. A red command is a typo."),
            Step("If your terminal does not support this, the shell falls back to a plain prompt. "
                 "Set MYSHELL_EDITOR=basic to choose that yourself."),
        ]),
        Lesson("customize", "Make it yours", "aliases, prompt and history", [
            Step("An alias is a short name for a longer command.",
                 "alias ll='ls -l'", run=False),
            Step("PS1 sets your prompt. \\w is the folder, \\u is your name, \\? is the last status. "
                 "More in the prompt lesson.",
                 "PS1='\\u \\w \\$ '", run=False),
            Step("Put aliases and settings in ~/.myshellrc and they load at every start. "
                 "Add GUIDE=off there if you do not want me."),
            Step("history lists earlier commands. history 5 shows the last five.",
                 "history 5"),
        ]),
    ]


CHEAT = """\
Run and combine     a | b      a && b      a || b      a; b      a &
Redirect            > file     >> file     < file      2> file   2>&1
Text input          <<< 'text'             <<EOF ... EOF
Variables           X=1        $X          ${X}        $?        $$
Capture and math    $(cmd)     `cmd`       $((1 + 2))
Quotes              'literal'  "with $X"   \\x
Wildcards           *   ?   [ab]   ~
Tests               [ -f file ]  [ -d dir ]  [ -z str ]  [ 1 -lt 2 ]  [ a = b ]
Functions           name() { cmds; }    $1 $2    $#    $@    "$@"    return N    local X
Expansions          ${X:-dflt}  ${#X}  ${X%.txt}  ${X#pre}  ${X/old/new}  ${X:1:3}  {a,b}  {1..5}
Choices             if C; then A; elif D; then B; else E; fi
Case                case $X in a|b) cmds;; *.txt) cmds;; *) cmds;; esac
Loops               for X in a b; do ..; done    while C; do ..; done    until C; do ..; done
Loop control        break    continue    break 2
Jobs                cmd &    jobs    fg    bg    wait    Ctrl+C    Ctrl+Z
Builtins            cd pwd echo printf read alias unalias type which export unset set env
                    source exec trap shift local history true false : exit
Files and text      ls cat head tr wc grep touch mkdir rm cp mv sleep
Set options         set -e (stop on error)   set -u (unset is error)   set -x (trace)
Typing              Ctrl+R search history    Right arrow takes the suggestion
Guide               guide    guide TOPIC    guide cheat    guide tip    guide quiz    guide progress"""

TIPS = [
    "Press Tab to complete a command or a file name.",
    "Press the up arrow to bring back your last command.",
    "cd - takes you back to the folder you were in before.",
    "Use && to run a second command only when the first one works.",
    "echo $? shows whether your last command worked. 0 means yes.",
    "Wrap a variable in double quotes when it may hold spaces.",
    "history 10 shows your last ten commands.",
    "Put aliases in ~/.myshellrc and they load every time you start.",
    "Use type NAME to see whether a name is a builtin, an alias or a program.",
    "Press Ctrl+Z to pause a program. Then bg or fg decides what happens next.",
    "Use $((a + b)) for quick math right in the prompt.",
    "A trailing & runs a command in the background.",
    "Press Ctrl+R and type a few letters to search your history.",
    "Use ${NAME:-default} to fall back to a default value.",
    "Use {1..5} or file.{txt,md} to save typing.",
    "Try guide quiz to test what you know.",
    "Wrap repeated commands in a function: name() { echo hi; }.",
    "Type guide progress to see which lessons you finished.",
]


def tip() -> str:
    return random.choice(TIPS)


# --- hints -----------------------------------------------------------------

_path_cache: dict = {}


def known_commands() -> List[str]:
    """Builtins, aliases are added by the caller, and every program on PATH."""
    from .builtins import builtin_names
    path = os.environ.get("PATH", "")
    if path not in _path_cache:
        names = set()
        for folder in path.split(os.pathsep):
            try:
                for entry in os.listdir(folder or "."):
                    full = os.path.join(folder, entry)
                    if os.path.isfile(full) and os.access(full, os.X_OK):
                        names.add(entry)
            except OSError:
                pass
        _path_cache.clear()
        _path_cache[path] = sorted(names)
    return sorted(set(builtin_names()) | set(_path_cache[path]))


def missing_hint(name: str, state: ShellState) -> Optional[str]:
    """What Pip says after `name: command not found`. None when the program exists."""
    if "/" in name or shutil.which(name):
        return None
    candidates = known_commands() + list(state.aliases)
    close = difflib.get_close_matches(name, candidates, n=3, cutoff=0.7)
    if close:
        return (f"I do not know '{name}'. Did you mean '{close[0]}'?"
                if len(close) == 1 else
                f"I do not know '{name}'. Did you mean " +
                ", ".join(f"'{c}'" for c in close) + "?")
    return f"I could not find '{name}'. Check the spelling, or install the program."


SYNTAX_HINTS = [
    ("unterminated quote", "A quote was opened and never closed. Add the matching quote."),
    ("unterminated command substitution",
     "A $( or a backtick was opened and never closed. Add the closing ) or backtick."),
    ("unexpected end of file", "A block is still open. End it with fi or done."),
    ("unexpected end of line",
     "The line ended too early. Something must follow the last operator."),
    ("unexpected token",
     "The shell hit a symbol it did not expect. Operators such as | and && need a command "
     "on both sides, and if needs then and fi."),
]


def syntax_hint(message: str) -> Optional[str]:
    for fragment, hint in SYNTAX_HINTS:
        if fragment in message:
            return hint
    return None


# --- the builtin and the interactive menu ---------------------------------

Ask = Callable[[str], Optional[str]]


def make_ask() -> Ask:
    """Read one line. Returns None at end of input. Answers stay out of the history."""
    def ask(prompt: str) -> Optional[str]:
        try:
            before = history.length()
            text = input(prompt)
            if history.records_input() and history.length() > before:
                history.forget_last()
            return text.strip()
        except EOFError:
            return None
    return ask


def run_lesson(state: ShellState, lesson: Lesson, out: TextIO, ask: Ask) -> bool:
    """Teach one lesson. Returns False when the person stops or input ends."""
    say(f"{lesson.title}: {lesson.summary}.", "happy", out)
    for step in lesson.steps:
        say(step.text, "think", out)
        if not step.command:
            continue
        for line in step.command.split("\n"):
            print(f"      $ {line}", file=out)
        if not step.run:
            print("      (shown only. Type it yourself when you want it.)", file=out)
            continue
        answer = ask("      Enter runs it. s skips. q stops > ")
        if answer is None or answer.lower() in ("q", "quit"):
            say("Okay. Type guide when you want to continue.", "happy", out)
            return False
        if answer.lower() == "s":
            continue
        saved = state.last_status
        try:
            state.execute(step.command)
        except Exception as e:  # a lesson must never close the shell
            print(e, file=out)
        state.last_status = saved
        print(file=out)
    progress.mark_lesson(state.progress_file, lesson.key)
    say("That is the topic. Pick another one, or press q. Try guide quiz to test yourself.",
        "wow", out)
    return True


def find_lesson(lessons: List[Lesson], choice: str) -> Optional[Lesson]:
    choice = choice.strip().lower()
    if choice.isdigit() and 1 <= int(choice) <= len(lessons):
        return lessons[int(choice) - 1]
    for lesson in lessons:
        if choice == lesson.key or (len(choice) >= 3 and lesson.key.startswith(choice)):
            return lesson
    return None


def print_topics(lessons: List[Lesson], out: TextIO, state: Optional[ShellState] = None) -> None:
    done = set(progress.done_lessons(state.progress_file)) if state else set()
    for i, lesson in enumerate(lessons, 1):
        mark = "[x]" if lesson.key in done else "[ ]"
        print(f"  {i:>2}) {lesson.title:<24} {mark} {lesson.summary}", file=out)
    print("   c) Cheat sheet", file=out)
    print("   z) Quiz", file=out)
    print("   p) Progress", file=out)
    print("   q) Quit", file=out)


# --- quiz and progress -----------------------------------------------------

LETTERS = "abcd"


def run_quiz(state: ShellState, out: TextIO, ask: Ask, topic: Optional[str] = None,
             count: int = 5, rng: Optional[random.Random] = None) -> int:
    """Ask count multiple-choice questions. Answer with a letter or a number. q stops."""
    questions = quiz.pick(count, topic, rng)
    if not questions:
        say("I have no questions for that topic.", "worried", out)
        return 1
    say(f"Quiz time: {len(questions)} question{'s' if len(questions) != 1 else ''}"
        + (f" about {topic}" if topic else "") + ". Answer with a letter. q stops.", "think", out)
    score = 0
    for number, item in enumerate(questions, 1):
        print(f"\n{number}. {item.text}", file=out)
        for letter, choice in zip(LETTERS, item.choices):
            print(f"   {letter}) {choice}", file=out)
        while True:
            answer = ask("   Your answer > ")
            if answer is None or answer.lower() in ("q", "quit"):
                say("Okay. No score saved for a quiz you stop.", "happy", out)
                return 0
            key = answer.lower()[:1]
            if key in LETTERS[:len(item.choices)]:
                picked = LETTERS.index(key)
            elif key.isdigit() and 1 <= int(key) <= len(item.choices):
                picked = int(key) - 1
            else:
                print("   Type a, b, c or d.", file=out)
                continue
            break
        if picked == item.answer:
            score += 1
            say(f"Right. {item.why}", "wow", out)
        else:
            say(f"Not quite. The answer is {LETTERS[item.answer]}) {item.choices[item.answer]}. "
                f"{item.why}", "worried", out)
    best = progress.record_quiz(state.progress_file, topic or "all", score, len(questions))
    ending = " That is your best score yet." if best and score else ""
    say(f"You got {score} of {len(questions)}.{ending}",
        "wow" if score == len(questions) else "happy", out)
    return 0


def show_progress(state: ShellState, out: TextIO) -> None:
    lessons = build_lessons()
    done = set(progress.done_lessons(state.progress_file))
    finished = [x for x in lessons if x.key in done]
    print(f"Lessons finished: {len(finished)} of {len(lessons)}", file=out)
    for lesson in lessons:
        print(f"  {'[x]' if lesson.key in done else '[ ]'} {lesson.title}", file=out)
    scores = progress.best_scores(state.progress_file)
    if scores:
        print("Best quiz scores:", file=out)
        for topic in sorted(scores):
            got, total = scores[topic]
            print(f"  {topic:<12} {got} of {total}", file=out)
    else:
        print("No quiz taken yet. Try guide quiz.", file=out)
    left = [x.key for x in lessons if x.key not in done]
    if left:
        say(f"Next up: {left[0]}. Type guide {left[0]}.", "happy", out)
    else:
        say("You finished every lesson. Keep going with guide quiz.", "wow", out)


def parse_quiz_args(args: List[str]):
    """guide quiz [TOPIC] [COUNT]. Returns (topic, count) or an error text."""
    topic, count = None, 5
    for a in args:
        if a.isdigit():
            count = max(1, min(int(a), 20))
        elif a in quiz.topics():
            topic = a
        else:
            matches = [t for t in quiz.topics() if len(a) >= 3 and t.startswith(a.lower())]
            if len(matches) != 1:
                return f"unknown quiz topic '{a}'. Topics: " + ", ".join(quiz.topics())
            topic = matches[0]
    return topic, count


def run_menu(state: ShellState, out: TextIO, ask: Ask) -> int:
    lessons = build_lessons()
    say("Hi, I am Pip. I show what this shell does with examples you can run. "
        "Pick a topic.", "happy", out)
    while True:
        print_topics(lessons, out, state)
        choice = ask("Pick one > ")
        if choice is None or choice.lower() in ("q", "quit", "exit"):
            say("See you soon. Type guide any time.", "happy", out)
            return 0
        if choice.lower() in ("c", "cheat"):
            print(CHEAT, file=out)
            continue
        if choice.lower() in ("z", "quiz"):
            run_quiz(state, out, ask)
            continue
        if choice.lower() in ("p", "progress"):
            show_progress(state, out)
            continue
        lesson = find_lesson(lessons, choice)
        if lesson is None:
            say("I do not know that one. Type a number from the list.", "worried", out)
            continue
        if not run_lesson(state, lesson, out, ask):
            return 0


def guide_enabled(state: ShellState) -> bool:
    return (state.get_variable("GUIDE") or "").lower() != "off"


def greeting(state: ShellState, out: TextIO) -> None:
    say("Hi, I am Pip, your shell guide. Type guide for a tour, or start typing. "
        f"Tip: {tip()}", "happy", out)


def guide_main(state: ShellState, args: List[str], stdout: TextIO, stderr: TextIO) -> int:
    if not args:
        return run_menu(state, stdout, make_ask())
    word = args[0].lower()
    if word == "quiz":
        parsed = parse_quiz_args(args[1:])
        if isinstance(parsed, str):
            print(f"guide: {parsed}", file=stderr)
            return 1
        return run_quiz(state, stdout, make_ask(), parsed[0], parsed[1])
    if word == "progress" and args[1:] == ["reset"]:
        progress.reset(state.progress_file)
        say("Progress cleared.", "happy", stdout)
        return 0
    if len(args) > 1:
        print("guide: too many arguments", file=stderr)
        return 1
    lessons = build_lessons()
    if word == "progress":
        show_progress(state, stdout)
        return 0
    if word == "list":
        print_topics(lessons, stdout, state)
        return 0
    if word == "cheat":
        print(CHEAT, file=stdout)
        return 0
    if word == "tip":
        say(tip(), "happy", stdout)
        return 0
    if word in ("on", "off"):
        state.set_variable("GUIDE", word)
        say("Hints are on. I will greet you and explain mistakes." if word == "on"
            else "Hints are off. Type guide on to bring me back.", "happy", stdout)
        return 0
    lesson = find_lesson(lessons, word)
    if lesson is None:
        print(f"guide: unknown topic '{args[0]}'. Type guide list to see the topics.",
              file=stderr)
        return 1
    run_lesson(state, lesson, stdout, make_ask())
    return 0
