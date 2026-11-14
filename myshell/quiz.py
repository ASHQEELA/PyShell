"""Pip's quiz questions. Each topic matches a lesson key."""
import random
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Question:
    topic: str
    text: str
    choices: List[str]
    answer: int  # index into choices
    why: str


def q(topic: str, text: str, choices: List[str], answer: int, why: str) -> Question:
    return Question(topic, text, choices, answer, why)


QUESTIONS: List[Question] = [
    q("basics", "Which command shows the folder you are in?",
      ["pwd", "cd", "echo", "ls"], 0, "pwd means print working directory."),
    q("basics", "What does cd - do?",
      ["Goes to your home folder", "Goes back to the previous folder", "Deletes a folder",
       "Lists the folder"], 1, "cd - jumps to the folder you were in before."),
    q("basics", "Which key completes a command or a file name?",
      ["Enter", "Tab", "Esc", "Space"], 1, "Tab completes. Press it twice to see the choices."),
    q("pipes", "What does a pipe (|) do?",
      ["Saves output to a file", "Sends the output of one command into the next",
       "Runs a command in the background", "Ends a command"], 1,
      "a | b feeds the output of a into b."),
    q("pipes", "Which operator adds text to the end of a file?",
      [">", ">>", "<", "|"], 1, ">> appends. > replaces the file."),
    q("pipes", "What does 2>&1 do?",
      ["Sends error messages to the same place as normal output",
       "Hides error messages", "Runs a command twice", "Reads input from file 2"], 0,
      "File 2 is the error stream and file 1 is normal output."),
    q("variables", "How do you read a variable called name?",
      ["name", "$name", "#name", "(name)"], 1, "A dollar sign reads the value."),
    q("variables", "What does echo $((6 * 7)) print?",
      ["6 * 7", "13", "42", "67"], 2, "$(( )) does the math."),
    q("variables", "What does $? hold?",
      ["The current folder", "The exit status of the last command",
       "The number of arguments", "The shell name"], 1, "0 means success. Anything else is a failure."),
    q("quotes", "What does echo '$HOME' print?",
      ["Your home folder", "$HOME", "Nothing", "An error"], 1,
      "Single quotes keep every character as typed."),
    q("quotes", "Why wrap a variable in double quotes?",
      ["It runs faster", "It keeps spaces in the value together",
       "It makes the variable global", "It deletes the variable"], 1,
      "Without quotes the shell splits the value at spaces."),
    q("logic", "When does the command after && run?",
      ["Always", "When the first command fails", "When the first command succeeds",
       "Never"], 2, "&& means and. || runs the next command after a failure."),
    q("logic", "Which loop runs once for each item in a list?",
      ["while", "for", "until", "if"], 1, "for NAME in LIST; do ...; done"),
    q("logic", "What does break do inside a loop?",
      ["Skips to the next round", "Leaves the loop", "Stops the shell", "Pauses the loop"], 1,
      "continue skips to the next round. break leaves."),
    q("jobs", "What does a trailing & do?",
      ["Runs the command in the background", "Repeats the command",
       "Hides the output", "Joins two commands"], 0, "The prompt returns at once."),
    q("jobs", "Which key combination stops a running command?",
      ["Ctrl+C", "Ctrl+D", "Ctrl+L", "Ctrl+A"], 0, "Ctrl+C sends an interrupt."),
    q("customize", "Where do you put aliases so they load at every start?",
      ["~/.myshellrc", "/etc/hosts", "~/Documents", "PATH"], 0,
      "The rc file runs each time an interactive shell starts."),
    q("customize", "Which prompt escape shows the current folder?",
      [r"\w", r"\u", r"\h", r"\$"], 0, r"\u is the user name and \h is the host."),
    q("functions", "How do you write the first argument inside a function?",
      ["$0", "$1", "$#", "$@"], 1, "$1 is the first argument. $0 is the script name."),
    q("functions", "What does $# hold?",
      ["The last exit status", "The number of arguments", "All the arguments", "The process ID"],
      1, "$@ holds all the arguments."),
    q("functions", "What does return 3 do in a function?",
      ["Prints 3", "Leaves the function with status 3", "Repeats it 3 times", "Exits the shell"],
      1, "return ends the function and sets $? to the number."),
    q("functions", 'What does "$@" give you?',
      ["All arguments as one word", "Each argument as its own word", "The first argument",
       "The count of arguments"], 1, 'The quotes keep each argument whole, even with spaces.'),
    q("functions", "Which keyword gives a variable a value that only lives in the function?",
      ["export", "local", "set", "unset"], 1, "local restores the old value when the function ends."),
    q("expansion", "What does ${NAME:-fallback} give when NAME is unset?",
      ["An error", "Nothing", "fallback", "NAME"], 2, ":- supplies a default."),
    q("expansion", "What does ${#word} give when word is hello?",
      ["hello", "5", "4", "0"], 1, "# in front asks for the length."),
    q("expansion", "With f=notes.txt, what does ${f%.txt} give?",
      ["notes", ".txt", "notes.txt", "txt"], 0, "% removes a matching ending."),
    q("expansion", "With s=a-b-c, what does ${s//-/+} give?",
      ["a+b-c", "a+b+c", "a-b-c", "+"], 1, "Two slashes replace every match. One replaces the first."),
    q("expansion", "What does echo {1..4} print?",
      ["{1..4}", "1 2 3 4", "1234", "4"], 1, "Brace expansion builds the list before the command runs."),
    q("expansion", "What does echo file.{txt,md} print?",
      ["file.{txt,md}", "file.txt file.md", "file.txtmd", "file."], 1,
      "Each item in the braces makes one word."),
    q("case", "What ends one arm of a case statement?",
      ["fi", ";;", "done", "esac"], 1, "esac ends the whole case. ;; ends one arm."),
    q("case", "Which pattern matches anything?",
      ["?", "*", "%", "!"], 1, "A lone * as the last pattern works as a default."),
    q("case", "How do you give one arm two patterns?",
      ["a,b)", "a|b)", "a&b)", "a b)"], 1, "Separate the patterns with a pipe."),
    q("tools", "Which builtin reads a line of input into a variable?",
      ["read", "scan", "input", "get"], 0, "read NAME stores the line in NAME."),
    q("tools", "Which command formats text with %s and %d?",
      ["echo", "printf", "format", "print"], 1, "printf repeats its format for extra arguments."),
    q("tools", "What does set -e do?",
      ["Prints every command", "Stops the script when a command fails",
       "Sets an error message", "Clears variables"], 1, "set -x prints every command. set -e stops on failure."),
    q("tools", "What does trap 'echo bye' EXIT do?",
      ["Runs echo now", "Runs echo when the shell ends", "Blocks Ctrl+C", "Defines a function"],
      1, "A trap runs its command when the signal or event arrives."),
    q("tools", "Which command shows where a program lives?",
      ["which", "where", "find", "path"], 0, "which prints the path, or says builtin, alias or function."),
    q("prompt", r"Which escape shows the time as HH:MM:SS?",
      [r"\t", r"\d", r"\w", r"\g"], 0, r"\d is the date and \g is the git branch."),
    q("prompt", "How does the shell find the git branch?",
      ["It runs git", "It reads the .git/HEAD file", "It asks the network", "It guesses"],
      1, "No git program is needed."),
    q("prompt", "Which variable sets how many seconds make a command slow?",
      ["SLOW_COMMAND_SECS", "SLOW", "TIMEOUT", "PS1"], 0, "Set it to off to hide the time notice."),
    q("typing", "Which key opens reverse history search?",
      ["Ctrl+R", "Ctrl+S", "Ctrl+H", "Ctrl+T"], 0, "Type part of an old command and it appears."),
    q("typing", "Which key accepts the grey suggestion?",
      ["Right arrow", "Left arrow", "Delete", "Tab twice"], 0, "End works too."),
]


def topics() -> List[str]:
    seen: List[str] = []
    for item in QUESTIONS:
        if item.topic not in seen:
            seen.append(item.topic)
    return seen


def pick(count: int, topic: Optional[str] = None,
         rng: Optional[random.Random] = None) -> List[Question]:
    """Choose count questions. With a topic, only that topic. Order and choices are shuffled."""
    rng = rng or random.Random()
    pool = [x for x in QUESTIONS if topic in (None, x.topic)]
    chosen = rng.sample(pool, min(count, len(pool)))
    shuffled = []
    for item in chosen:
        order = list(range(len(item.choices)))
        rng.shuffle(order)
        shuffled.append(Question(item.topic, item.text, [item.choices[i] for i in order],
                                 order.index(item.answer), item.why))
    return shuffled
