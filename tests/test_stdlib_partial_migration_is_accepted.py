"""A stdlib source may keep its import when only one of its calls was replaced.

A3 migrates ``datetime.fromisoformat`` to ciso8601. The parsed result is still
a ``datetime``, the module still needs ``timezone.utc`` and the ``-> datetime``
annotation, so ``from datetime import datetime, timezone`` legitimately
survives a CORRECT migration.

``_patch_integrity_errors`` demanded that the source import binding change,
which made A3 unwinnable: measured on two live runs, the Connector burned all
five repair cycles and the file was reverted to the original both times.

``library_swapped`` in the harness already draws the line the other way -- for
a stdlib source it asks whether the replaced CALL is gone. The acceptance gate
has to agree with the definition it is scored against, or it rejects
migrations the scorer would have credited.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.migration_completeness import STDLIB_SOURCES  # noqa: E402

CORPUS = os.path.expanduser("~/Downloads/evaluation")


def _errors(source: str, patched: str, original: str, winner: str):
    from smart_loader.agents.verifier_agent import _patch_integrity_errors

    class _D:
        def __init__(self):
            self.original = original
            self.winner = winner
            self.original_import = original
            self.winner_import = winner
    return _patch_integrity_errors(source, patched, [_D()])


def test_stdlib_table_is_shared_with_the_harness():
    """Both sides must agree on which sources get the exemption."""
    from smart_loader.eval.proteus_harness import _STDLIB_SOURCES
    assert STDLIB_SOURCES == _STDLIB_SOURCES, \
        "the gate and the scorer must use the same stdlib table"


def test_replaced_call_gone_is_accepted_though_the_import_remains():
    source = (
        "from datetime import datetime, timezone\n"
        "def parse(ts) -> datetime:\n"
        "    return datetime.fromisoformat(ts)\n"
    )
    patched = (
        "from datetime import datetime, timezone\n"
        "import ciso8601\n"
        "def parse(ts) -> datetime:\n"
        "    return ciso8601.parse_datetime(ts)\n"
    )
    messages = [e["message"] for e in _errors(source, patched, "datetime", "ciso8601")]
    assert not any("No import binding" in m for m in messages), messages


def test_replaced_call_still_present_is_still_rejected():
    """The check must keep working: an unmigrated file is not exempt."""
    source = (
        "from datetime import datetime, timezone\n"
        "def parse(ts) -> datetime:\n"
        "    return datetime.fromisoformat(ts)\n"
    )
    patched = source + "import ciso8601\n"     # target added, call untouched
    messages = [e["message"] for e in _errors(source, patched, "datetime", "ciso8601")]
    assert any("No import binding" in m for m in messages), messages


def test_non_stdlib_source_gets_no_exemption():
    """Only the stdlib table is exempt; a real package must lose its import."""
    source = "import requests\ndef f():\n    return requests.get('x')\n"
    patched = "import requests\nimport httpx\ndef f():\n    return httpx.get('x')\n"
    messages = [e["message"] for e in _errors(source, patched, "requests", "httpx")]
    assert any("No import binding" in m for m in messages), messages


def test_the_call_is_matched_in_code_not_prose():
    """A3's docstring names the replaced call; that must not block acceptance."""
    source = (
        "from datetime import datetime, timezone\n"
        "def parse(ts) -> datetime:\n"
        "    return datetime.fromisoformat(ts)\n"
    )
    patched = (
        '"""Migrated from datetime.fromisoformat (stdlib) to ciso8601."""\n'
        "from datetime import datetime, timezone\n"
        "import ciso8601\n"
        "def parse(ts) -> datetime:\n"
        "    return ciso8601.parse_datetime(ts)\n"
    )
    messages = [e["message"] for e in _errors(source, patched, "datetime", "ciso8601")]
    assert not any("No import binding" in m for m in messages), messages
