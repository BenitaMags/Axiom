"""``compiles`` must RUN the file, not merely parse it.

It used to be ``ast.parse`` alone. Syntactically valid text that cannot be
imported -- because it adopts a package nobody has installed, or uses a name at
module scope that no longer exists -- scored ``compiles=Y``. Two single_shot
clones did exactly that: one imported ``pid``, one imported ``envelopes``,
neither of which exists. Both were reported as compiling.

These cases pin the distinction so the column cannot quietly weaken again.
"""
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.eval.proteus_harness import Program, compiles


def _program(tmp_path, body: str, name: str = "subject.py") -> Program:
    (tmp_path / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return Program(name="T", category="C", path=str(tmp_path), from_lib="old", to_lib="new",
                   source_files=[name], test_files=[])


def test_importable_module_compiles(tmp_path):
    p = _program(tmp_path, """
        import json

        class Subject:
            def run(self):
                return json.dumps({"ok": True})
    """)
    assert compiles(str(tmp_path), p) is True


def test_missing_package_does_not_compile(tmp_path):
    """The exact shape of the two clones this test exists for."""
    p = _program(tmp_path, """
        import a_package_that_does_not_exist_anywhere

        class Subject:
            pass
    """)
    assert compiles(str(tmp_path), p) is False


def test_module_scope_name_error_does_not_compile(tmp_path):
    """Parses cleanly; raises the moment it is loaded."""
    p = _program(tmp_path, """
        DEFAULT = build_default()

        class Subject:
            pass
    """)
    assert compiles(str(tmp_path), p) is False


def test_syntax_error_still_does_not_compile(tmp_path):
    p = _program(tmp_path, "class Subject\n    pass\n")
    assert compiles(str(tmp_path), p) is False


def test_missing_file_does_not_compile(tmp_path):
    p = Program(name="T", category="C", path=str(tmp_path), from_lib="old", to_lib="new",
                source_files=["absent.py"], test_files=[])
    assert compiles(str(tmp_path), p) is False


def test_import_that_blocks_forever_does_not_compile(tmp_path):
    """A module that hangs on import is not usable, whatever the reason."""
    p = _program(tmp_path, """
        import time
        time.sleep(600)
    """)
    assert compiles(str(tmp_path), p, timeout=3) is False
