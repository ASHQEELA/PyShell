# myshell

A Unix-style shell written in Python. It runs on Linux and macOS with full job control. It also runs on Windows through a simpler fallback.

## Requirements

- Python 3 with the standard library only. Tested on Python 3.13.
- pytest and pyflakes for development, listed in `requirements.txt`

## Run it

With plain Python:

    python shell.py                  interactive shell
    python shell.py -c "echo hi"     run one command string
    python shell.py script.sh        run a script file

With uv (no install step, since the shell has no dependencies):

    uv run --no-project python shell.py
    uv run --no-project python shell.py -c "echo hi"

You also run it as a package with `python -m myshell`.

## Run the tests

With pip:

    pip install -r requirements.txt
    python -m pytest
    python -m pyflakes myshell tests

With uv, no virtual environment needed:

    uv run --no-project --with-requirements requirements.txt python -m pytest
    uvx pyflakes myshell tests

With uv and a virtual environment:

    uv venv
    uv pip install -r requirements.txt
    uv run pytest

## Features

Commands and operators
- Pipes: `a | b`
- Lists: `a; b`, `a && b`, `a || b`
- Background jobs: `a &`
- Comments with `#`

Quoting and expansion
- Single quotes, double quotes and backslash escapes
- Variables: `$X`, `${X}`, `$?`, `$$`
- Command substitution: `$(cmd)` and backticks
- Arithmetic: `$((1 + 2 * x))`, including `+=`, `?:`, hex and octal
- Word splitting, globbing (`*`, `?`, `[ab]`) and tilde (`~`)
- Assignments: `NAME=value`, and `NAME=value cmd` for one command

Redirection
- `< > >> 2> 2>> &> &>>`
- `2>&1` and `1>&2`, applied left to right
- Here-strings: `<<<`
- Here-documents: `<<EOF` and `<<-EOF`. A quoted delimiter such as `<<'EOF'` turns expansion off.

Control flow
- `if`, `elif`, `else`, `fi`
- `while`, `until`, `for NAME in WORDS`, with `do` and `done`
- `break N` and `continue N`
- Blocks work on one line or across many lines

Builtins
- `echo pwd cd exit alias type export unset source .`
- `jobs fg bg wait`
- `test` and `[ ]`: file, string and number checks, and `!`
- `history`: `N`, `-c`, `-d N`, `-w [file]`, `-r [file]`
- `guide`: lessons, cheat sheet and tips

Interactive use
- Job control: Ctrl+C, Ctrl+Z, `fg`, `bg`
- Tab completion for commands and paths
- History saved in `~/.custom_shell_history`, limited to 1000 entries
- Startup file `~/.myshellrc`
- Prompt set with `PS1`. Continuation lines use `PS2`.

## Pip, the guide

Pip is a small original character who lives in the shell.

    ,---.   .--------------------------------------.
   ( ^ ^ )  | Hi, I am Pip, your shell guide.       |
    \ v /   '--------------------------------------'
    (_@_)>

- Greets you at startup with a tip. Only on a real terminal.
- `guide` opens a menu of 7 lessons. Each step shows a command. Press Enter to run it, `s` to skip, `q` to stop.
- `guide TOPIC` jumps to one lesson. Names and numbers both work, for example `guide pipes` or `guide 2`.
- `guide cheat` prints a cheat sheet. `guide tip` prints one tip. `guide list` lists the topics.
- Explains mistakes. A mistyped command gets "Did you mean ...?". An unclosed quote, `$(` or block gets a plain explanation.
- `guide off` hides Pip for the session. Put `GUIDE=off` in `~/.myshellrc` to hide Pip always.
- Scripts, `-c` and piped input never show Pip's greeting or hints.

## Prompt escapes

| Escape | Meaning |
| --- | --- |
| `\w` | current directory, `~` for home |
| `\W` | last part of the directory |
| `\u` `\h` | user name, host name |
| `\$` | `$`, or `#` for root |
| `\j` | running jobs |
| `\?` | exit status of the last command |
| `\n` `\e` `\\` | newline, escape, backslash |
| `\[` `\]` | wrap colors so line editing counts width correctly |

Example `~/.myshellrc`:

    PS1='\u@\h \W \$ '
    PS2='more> '
    alias ll='ls -l'

## Layout

| File | Job |
| --- | --- |
| `cli.py` | entry point, interactive loop |
| `lexer.py` | text to tokens |
| `parser.py` | tokens to a tree |
| `expand.py` | variables, substitution, math, globbing |
| `arith.py` | the `$((...))` evaluator |
| `heredoc.py` | here-document bodies |
| `executor.py` | runs the tree with fork and pipes |
| `jobs.py` | process groups and job control |
| `builtins.py` | builtin commands |
| `completion.py` | tab completion |
| `prompt.py` | prompt escapes |
| `guide.py` | Pip: lessons, hints, greeting |
| `state.py` | variables, aliases, history, status |
| `compat.py` | platform checks |

## Limits

- No functions, positional parameters (`$1`, `$@`) or `case`.
- Only file descriptors 0, 1 and 2 are redirected.
- Here-documents do not work inside multi-line `if` and loop blocks.
- Pipes, redirects and `&` after `fi` or `done` are errors.
- `test` has no `-a`, `-o` or parentheses.
- On Windows, command substitution and job control are unavailable.
