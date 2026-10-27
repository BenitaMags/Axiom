__version__ = '0.3'
import re
try:
    import pycodestyle
except ImportError:
    import ruff as pycodestyle

def _pep8_noqa_wrapper(physical_line):
    """Wrapper to preserve pep8.noqa behavior using pycodestyle.

    pep8.noqa(physical_line) returns True if the line contains a noqa comment.
    pycodestyle.noqa(physical_line) has the identical API and behavior since
    pycodestyle is the direct successor to pep8 (renamed in 2016).
    This wrapper delegates to pycodestyle.noqa to maintain exact compatibility.
    """
    return pycodestyle.noqa(physical_line)
NOTE_REGEX = re.compile('(TODO|FIXME|XXX)')

def check_todo_notes(physical_line):
    if _pep8_noqa_wrapper(physical_line):
        return
    match = NOTE_REGEX.search(physical_line)
    if match:
        return (match.start(), 'T000 Todo note found.')
check_todo_notes.name = name = 'flake8-todo'
check_todo_notes.version = __version__