"""The connector rewrites recorded call sites itself, rather than trusting ACI.

On B2 the analysis pass identified the call site exactly --

    Found 1 call site(s):  L14: client = ModbusTcpClient(host, port=port)
    1 call site(s) need a wrapper: _axiom_modbus_client_wrapper  L14

-- and the ACI editing session then rewrote line 69 (``MeterReader.__init__``)
instead, leaving line 14 inside ``_default_client`` untouched. The orphaned
factory kept constructing pymodbus, so the import stayed live, the dead-import
sweep could not touch it, and the row scored lib_swapped=N while looking like a
finished migration.

The connector already knows the line, the call text and the wrapper name, so
this needs no model.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.core.ast_rewrite import (
    apply_call_site_rewrites, names_bound_from_root)
from smart_loader.agents.connector_agent import (
    _drop_unused_source_imports, _remaining_source_imports)

# B2's real source, with the wrapper the ACI session actually injected.
B2_AFTER_ACI = '''\
from registers import REGISTERS, decode


def _axiom_modbus_client_wrapper(host: str, port: int = 502, unit: int = 1):
    """Adapter injected by the ACI session."""
    from pyModbusTCP.client import ModbusClient

    return ModbusClient(host, port=port, unit_id=unit)


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = ModbusTcpClient(host, port=port)
    client.connect()
    return client
'''

REWRITES = [{"line": 14, "call": "ModbusTcpClient(host, port=port)",
             "wrapper_name": "_axiom_modbus_client_wrapper"}]


def test_the_orphaned_factory_call_site_is_rewritten():
    out, applied = apply_call_site_rewrites(B2_AFTER_ACI, REWRITES, "pymodbus")
    assert applied, "the recorded call site was not rewritten"
    assert "_axiom_modbus_client_wrapper(host, port=port)" in out
    assert "ModbusTcpClient(host, port=port)" not in out


def test_the_import_then_becomes_dead_and_is_swept():
    """The whole point: the rewrite is what lets the sweep remove the import."""
    out, _applied = apply_call_site_rewrites(B2_AFTER_ACI, REWRITES, "pymodbus")
    swept, dropped = _drop_unused_source_imports(out, {"pymodbus"})
    assert dropped, f"import still referenced after the rewrite: {swept}"
    assert _remaining_source_imports(swept, "pymodbus") == []


def test_arguments_are_passed_through_verbatim():
    """Never remapped. A wrong mapping is invisible on this corpus.

    The suites dependency-inject their clients, so ``_default_client`` is
    never executed by the tests OR by the oracle's probes — broken code there
    passes every gate we have.
    """
    out, _ = apply_call_site_rewrites(B2_AFTER_ACI, REWRITES, "pymodbus")
    assert "(host, port=port)" in out


def test_a_call_that_does_not_match_exactly_is_left_alone():
    """Anything ambiguous is left for the post-condition to report."""
    mismatched = [{"line": 14, "call": "ModbusTcpClient(host)",
                   "wrapper_name": "_axiom_modbus_client_wrapper"}]
    out, applied = apply_call_site_rewrites(B2_AFTER_ACI, mismatched, "pymodbus")
    assert applied == []
    assert out == B2_AFTER_ACI


def test_calls_not_reaching_the_old_library_are_untouched():
    """Guard against rewriting a same-named call from somewhere else."""
    assert names_bound_from_root(B2_AFTER_ACI, "pymodbus") == {"ModbusTcpClient"}
    assert "ModbusClient" not in names_bound_from_root(B2_AFTER_ACI, "pymodbus")


def test_malformed_rewrite_entries_are_skipped():
    """`call_rewrites` is not uniformly shaped across the connector's paths."""
    out, applied = apply_call_site_rewrites(
        B2_AFTER_ACI, ["a plain string", {"no": "useful keys"}], "pymodbus")
    assert applied == [] and out == B2_AFTER_ACI


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
