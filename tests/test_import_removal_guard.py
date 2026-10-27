"""Removing the source import is only an overreach while the file still uses it.

The IMPORT LOCK guard vetoed ANY edit that dropped the source import. On B2 the
editing session rewrote `_default_client` to call the injected wrapper and
dropped the now-dead `from pymodbus.client import ModbusTcpClient` — a finished
migration — and the guard reverted all five injected wrappers for it. The repair
cycle that followed rebuilt the same work: 12 LLM calls and $0.39 for a run that
needed about half.

The deterministic post-processing would have deleted that import itself, as
dead. So the veto now applies only to a LIVE removal — one that leaves dangling
references.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.connector_agent import _connector_edit_candidate_violation

BEFORE = '''\
from registers import REGISTERS, decode


def _default_client(host, port=502):
    from pymodbus.client import ModbusTcpClient

    client = ModbusTcpClient(host, port=port)
    client.connect()
    return client
'''

# What B2's edit actually produced: wrapper injected, factory rewritten,
# dead import dropped. Previously reverted.
COMPLETE = '''\
from registers import REGISTERS, decode


def _axiom_modbus_client_wrapper(host, port=502):
    from pyModbusTCP.client import ModbusClient

    return ModbusClient(host=host, port=port)


def _default_client(host, port=502):
    client = _axiom_modbus_client_wrapper(host, port=port)
    client.connect()
    return client
'''

# Import dropped but the call site left behind — this would NameError.
DANGLING = BEFORE.replace("    from pymodbus.client import ModbusTcpClient\n\n", "")


def test_a_completed_migration_may_drop_the_dead_import():
    violation = _connector_edit_candidate_violation(
        BEFORE, COMPLETE, original_import="pymodbus",
        foreign_candidates=["minimalmodbus", "modbus_tk"])
    assert violation is None, f"correct edit rejected: {violation}"


def test_dropping_the_import_while_still_using_it_is_still_rejected():
    violation = _connector_edit_candidate_violation(
        BEFORE, DANGLING, original_import="pymodbus",
        foreign_candidates=["minimalmodbus"])
    assert violation is not None
    message, kind = violation
    assert kind == "generation"
    assert "ModbusTcpClient" in message, "the dangling name must be named"


def test_an_unselected_candidate_is_still_caught_after_a_legal_removal():
    """Relaxing the import rule must not open a hole in the identity check."""
    smuggled = COMPLETE.replace("from pyModbusTCP.client import ModbusClient",
                                "from minimalmodbus import Instrument")
    violation = _connector_edit_candidate_violation(
        BEFORE, smuggled, original_import="pymodbus",
        foreign_candidates=["minimalmodbus", "modbus_tk"])
    assert violation is not None
    assert violation[1] == "candidate_identity"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
