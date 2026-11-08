"""Here-documents: reading the body that follows a line with << or <<-."""
import sys
from typing import Callable, List, Optional

ReadLine = Callable[[], Optional[str]]


def read_heredoc_body(delimiter: str, strip_tabs: bool,
                      read_line: Optional[ReadLine]) -> str:
    """Read lines until one equals the delimiter. Returns the body with a newline
    after each line. With strip_tabs (<<-), leading tabs are removed from every line,
    including the delimiter line. If input ends first, the body so far is used and a
    warning goes to stderr."""
    lines: List[str] = []
    while True:
        line = read_line() if read_line is not None else None
        if line is None:
            print(f"myshell: warning: here-document delimited by end-of-file "
                  f"(wanted `{delimiter}')", file=sys.stderr)
            break
        if strip_tabs:
            line = line.lstrip("\t")
        if line == delimiter:
            break
        lines.append(line)
    return "".join(line + "\n" for line in lines)
