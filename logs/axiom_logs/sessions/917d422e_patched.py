__version__ = '0.3'
import re
import ruff
NOTE_REGEX = re.compile('(TODO|FIXME|XXX)')

def _ruff_noqa_wrapper(physical_line):
    """
    Wrapper to check if a physical line contains a '# noqa' comment.

    pep8.noqa() returned True if the line contains a '# noqa' comment
    (optionally followed by error codes like '# noqa: E123,W456').

    ruff does not expose a public `noqa` function. This wrapper replicates
    the behavior by checking for a '# noqa' pattern in the line.
    """
    return bool(re.search('#\\s*noqa\\b', physical_line))

def check_todo_notes(physical_line):
    if _ruff_noqa_wrapper(physical_line):
        return
    match = NOTE_REGEX.search(physical_line)
    if match:
        return (match.start(), 'T000 Todo note found.')
check_todo_notes.name = name = 'flake8-todo'
check_todo_notes.version = __version__