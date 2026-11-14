"""Exceptions used across the shell."""


class ShellError(Exception):
    """A user-facing error. The message is printed and the shell keeps running."""


class ShellSyntaxError(ShellError):
    def __str__(self) -> str:
        return f"syntax error: {self.args[0]}"


class ShellExit(Exception):
    """Raised by the exit builtin. The main loop catches it and shuts down."""
    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


class LoopControl(Exception):
    """Raised by break and continue. `levels` is how many enclosing loops it affects."""

    def __init__(self, levels: int = 1):
        super().__init__(levels)
        self.levels = levels


class BreakLoop(LoopControl):
    pass


class ContinueLoop(LoopControl):
    pass


class ReturnFromFunction(Exception):
    """Raised by return. The function call or sourced file catches it."""

    def __init__(self, status: int):
        super().__init__(status)
        self.status = status
