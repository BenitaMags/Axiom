"""A migration must not leave the old distribution imported and in use.

The failure this pins came from the ACI edit's IMPORT LOCK. Told not to touch
imports, the model rewrote the class's call site to use the injected adapter
and left the default-factory helper still constructing the old library:

    def _default_client(host, port=502):
        from pymodbus.client import ModbusTcpClient   # still used below
        client = ModbusTcpClient(host, port=port)

The dead-import sweep cannot remove that — the name IS referenced — so the
clone still needs `pymodbus` installed, and the row scored lib_swapped=N while
looking like a completed migration.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.connector_agent import _remaining_source_imports

ORPHANED_FACTORY = '''\
from registers import REGISTERS, decode
import pyModbusTCP.client


def _axiom_modbus_client_wrapper(host, port=502, unit=1):
    from pyModbusTCP.client import ModbusClient

    return ModbusClient(host, port=port, unit_id=unit)


def _default_client(host, port=502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = ModbusTcpClient(host, port=port)
    client.connect()
    return client


class MeterReader:
    def __init__(self, host=None, client=None, unit=1):
        self.client = client if client is not None else _axiom_modbus_client_wrapper(host, unit=unit)
'''

COMPLETED = ORPHANED_FACTORY.replace(
    '''def _default_client(host, port=502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = ModbusTcpClient(host, port=port)
    client.connect()
    return client''',
    '''def _default_client(host, port=502):
    return _axiom_modbus_client_wrapper(host, port=port)''')


def test_a_live_source_import_is_reported_with_its_line():
    survivors = _remaining_source_imports(ORPHANED_FACTORY, "pymodbus")
    assert survivors, "an orphaned factory still using the old library must be caught"
    assert "pymodbus" in survivors[0]
    assert survivors[0].startswith("line "), "the offending line must be named"


def test_a_completed_migration_reports_nothing():
    assert _remaining_source_imports(COMPLETED, "pymodbus") == []


def test_the_target_import_is_not_mistaken_for_the_source():
    """`pyModbusTCP` must never be flagged as a surviving `pymodbus` import.

    Both start with "pymodbus" case-insensitively up to a point; matching on
    prefixes rather than the full root would reject every correct migration.
    """
    assert _remaining_source_imports(COMPLETED, "pyModbusTCP") != []
    assert _remaining_source_imports(COMPLETED, "pymodbus") == []


def test_function_scope_imports_are_seen():
    """Nine of twelve corpus programs hide the import inside a function."""
    lazy = "def f():\n    import paho.mqtt.client as mqtt\n    return mqtt.Client()\n"
    assert _remaining_source_imports(lazy, "paho")


def test_unparseable_source_is_not_reported_as_a_violation():
    assert _remaining_source_imports("def broken(:\n", "pymodbus") == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
