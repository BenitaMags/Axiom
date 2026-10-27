"""A wrapper name the model wrote as "A / B" must not be reported unused.

Measured on a live D1 run: the analysis pass named its wrapper
``"_axiom_modbus_tcp_client_wrapper / _AxiomModbusTkClientAdapter"`` -- a
factory and the adapter class it builds, joined with " / ". That string is
never an ``ast.Name``, so the guard reported it unused on every attempt and the
Connector reverted its own CORRECT edit five times: same input, same output,
the entire repair budget and ~190k tokens spent reaching the identical result.

A false positive here is worse than a missed detection, because it discards a
working adapter and burns every remaining repair cycle. The detection itself
still has to work, so the dead-factory and dead-adapter cases below are as
load-bearing as the passing one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.connector_agent import _unused_wrapper_names  # noqa: E402

COMPOUND = "_axiom_modbus_tcp_client_wrapper / _AxiomModbusTkClientAdapter"

WIRED = """
class _AxiomModbusTkClientAdapter:
    def __init__(self, master):
        self._master = master

def _axiom_modbus_tcp_client_wrapper(host, port=502):
    from modbus_tk import modbus_tcp
    return _AxiomModbusTkClientAdapter(modbus_tcp.TcpMaster(host=host, port=port))

def _default_client(host, port=502):
    client = _axiom_modbus_tcp_client_wrapper(host, port=port)
    client.connect()
    return client
"""


def test_compound_name_fully_wired_is_not_reported():
    """The exact shape that cost five repair cycles."""
    assert _unused_wrapper_names(WIRED, [COMPOUND]) == []


def test_compound_name_with_a_dead_factory_is_still_caught():
    """The adapter is referenced inside the factory, but nothing calls the factory.

    "any part referenced" passes this, which is why the rule is "all".
    """
    source = WIRED.replace("client = _axiom_modbus_tcp_client_wrapper(host, port=port)",
                           "client = ModbusTcpClient(host, port=port)")
    assert _unused_wrapper_names(source, [COMPOUND]) == [COMPOUND]


def test_compound_name_with_a_dead_adapter_is_still_caught():
    source = WIRED.replace(
        "return _AxiomModbusTkClientAdapter(modbus_tcp.TcpMaster(host=host, port=port))",
        "return modbus_tcp.TcpMaster(host=host, port=port)")
    assert _unused_wrapper_names(source, [COMPOUND]) == [COMPOUND]


def test_ordinary_wired_name_is_not_reported():
    assert _unused_wrapper_names(WIRED, ["_axiom_modbus_tcp_client_wrapper"]) == []


def test_ordinary_unwired_name_is_still_caught():
    """The original behaviour must survive: this is what the guard is for."""
    assert _unused_wrapper_names(WIRED, ["_never_called"]) == ["_never_called"]


def test_name_with_no_defined_identifier_falls_back_to_the_literal_check():
    assert _unused_wrapper_names(WIRED, ["nothing / here"]) == ["nothing / here"]


def test_syntax_error_reports_nothing():
    assert _unused_wrapper_names("def f(\n", [COMPOUND]) == []
