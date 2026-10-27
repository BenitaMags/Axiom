"""A module the variant EXTRACTS is first-party, and may hold the migration.

The Logic Substitution Agent can answer a restructuring request by splitting a
module in two. When it does, the file it writes is not in
``program.source_files`` -- and three scorers iterated exactly that list, so
they stopped seeing whatever moved into the new file.

Measured on a D1 clone whose ``transport.py`` became ``transport.py`` +
``modbus_shim.py``, carrying the only ``import modbus_tk`` with it::

    chosen_target     -> 'clamp_utils'   # an extracted helper, first in sort order
    target_importable -> 'N'             # for a package that is installed
    target_api_used   -> True            # matched clamp_utils' own `clamp`

The last one is the damaging one. ``migrated`` calls ``target_api_used`` to
decide "is the adopted library actually CALLED, not merely imported" -- the
check that catches a clone importing httpx while still calling ``requests``.
Here it returned True by finding a first-party helper, so ``migration_success``
was granted to a row whose library usage had never been tested.

These tests pin the two halves that have to hold together: an extracted module
is not a candidate library, AND its contents are still searched for the real
one.
"""
import os
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import smart_loader.eval.proteus_harness as H  # noqa: E402


def _write(directory, **files):
    os.makedirs(directory, exist_ok=True)
    for name, body in files.items():
        with open(os.path.join(directory, name.replace("__", ".")), "w",
                  encoding="utf-8") as fh:
            fh.write(textwrap.dedent(body).lstrip())


@pytest.fixture
def split_clone(tmp_path):
    """An original importing pymodbus; a clone that migrated via a new module."""
    original, clone = str(tmp_path / "orig"), str(tmp_path / "clone")
    _write(original, transport__py="""
        from pymodbus.client import ModbusTcpClient

        def read(host):
            return ModbusTcpClient(host).read_holding_registers(0, 1)
    """)
    _write(clone, transport__py="""
        from modbus_shim import read_registers

        def read(host):
            return read_registers(host, 0, 1)
    """, modbus_shim__py="""
        import modbus_tk.modbus_tcp as modbus_tcp
        import modbus_tk.defines as cst

        def read_registers(host, address, count):
            master = modbus_tcp.TcpMaster(host=host)
            return master.execute(1, cst.READ_HOLDING_REGISTERS, address, count)
    """, helpers__py="""
        def clamp(value, low, high):
            return max(low, min(high, value))
    """)
    program = H.Program(
        name="X_split", path=original, category="D",
        from_lib="pymodbus", to_lib="modbus-tk",
        source_files=["transport.py"], test_files=[])
    return program, clone


def test_extracted_module_is_not_mistaken_for_the_adopted_library(split_clone):
    program, clone = split_clone
    assert H.chosen_target(clone, program) == "modbus_tk"


def test_library_imported_only_inside_an_extracted_module_counts_as_used(split_clone):
    program, clone = split_clone
    assert H.target_api_used(clone, program) is True
    assert H.library_swapped(clone, program) == "Y"


def test_first_party_helper_alone_does_not_prove_the_library_is_used(tmp_path):
    """The regression that inflated migration_success, in isolation.

    The clone below dropped ``pymodbus`` and extracted a helper it really does
    call -- but it never adopted any library. Nothing here should read as a
    completed migration.
    """
    original, clone = str(tmp_path / "orig"), str(tmp_path / "clone")
    _write(original, control__py="""
        import pymodbus

        def step(x):
            return max(0.0, min(100.0, x))
    """)
    _write(clone, control__py="""
        from clamp_utils import clamp

        def step(x):
            return clamp(x, 0.0, 100.0)
    """, clamp_utils__py="""
        def clamp(value, low, high):
            return max(low, min(high, value))
    """)
    program = H.Program(
        name="X_helper", path=original, category="D",
        from_lib="pymodbus", to_lib="minimalmodbus",
        source_files=["control.py"], test_files=[])

    assert H.chosen_target(clone, program) == ""
    assert H.target_api_used(clone, program) is False
    assert not H.migrated(original, clone, program)


def test_old_dependency_hiding_in_an_extracted_module_is_still_found(tmp_path):
    """Moving the old import into a new file must not read as removing it."""
    original, clone = str(tmp_path / "orig"), str(tmp_path / "clone")
    _write(original, sink__py="""
        import pymodbus

        def send(x):
            return pymodbus.write(x)
    """)
    _write(clone, sink__py="""
        from legacy_io import write

        def send(x):
            return write(x)
    """, legacy_io__py="""
        import pymodbus

        def write(x):
            return pymodbus.write(x)
    """)
    program = H.Program(
        name="X_hidden", path=original, category="D",
        from_lib="pymodbus", to_lib="minimalmodbus",
        source_files=["sink.py"], test_files=[])

    assert H.library_swapped(clone, program) == "N"


def test_clone_test_files_are_not_scanned_as_program_source(tmp_path):
    """A test's own imports are not the program's; the suite runs separately."""
    original, clone = str(tmp_path / "orig"), str(tmp_path / "clone")
    _write(original, app__py="""
        import pymodbus

        def go():
            return pymodbus.ping()
    """)
    _write(clone, app__py="""
        def go():
            return 1
    """, test_app__py="""
        import pytest_httpserver

        def test_go():
            assert True
    """)
    program = H.Program(
        name="X_tests", path=original, category="D",
        from_lib="pymodbus", to_lib="minimalmodbus",
        source_files=["app.py"], test_files=["test_app.py"])

    assert H.chosen_target(clone, program) == ""
