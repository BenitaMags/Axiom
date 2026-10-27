"""A wrapper must not report a status it never checked.

The third shape in this family, after `_blanket_except_sites` (catches
everything and swallows it) and `_raw_client_returns` (never sees the failure
at all). Here the adapter DOES call the library, discards the result, and
returns a success it never observed.

Measured on two of the six rows where verification made a blind judge's verdict
WORSE than the same program and seed without it. Both are the same two lines::

    B3  self._run(self._client.publish(topic, payload, qos=qos))
        return _PublishResult(0)

    D3  client._run(client._client.publish(topic, payload))
        return 0

Both originals returned ``getattr(info, "rc", 0)`` -- paho's real result code,
non-zero when the client is disconnected. After the rewrite ``rc`` can only
ever be 0, so a caller checking it is checking a literal. The judge rated both
MAJOR; the same programs without the Verifier scored MINOR.

The prompt has forbidden this since the first pilot ("Do NOT fabricate a
success code you cannot justify") and it happened anyway.

The exemption that matters: EXCEPTION-AS-STATUS is the correct adapter. When
the discarded call sits in a `try` whose handler returns a failure status,
"no exception raised" IS the success the constant reports -- the information
came from the call, through control flow. D1's faithful modbus wrapper is
exactly that, and flagging it would push the Connector to break a correct
adapter: the error this project has already made three times under other names.
"""
import os
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.agents.connector_agent import (  # noqa: E402
    _fabricated_status_returns)


def _s(code):
    return textwrap.dedent(code).lstrip()


# ------------------------------------------------------- the measured cases ---

def test_a_discarded_call_with_a_constant_return_is_flagged():
    found = _fabricated_status_returns(_s("""
        def publish(self, topic, payload, qos=0):
            self._run(self._client.publish(topic, payload, qos=qos))
            return _PublishResult(0)
    """), ["publish"])
    assert len(found) == 1
    assert "_PublishResult(0)" in found[0]


def test_a_bare_constant_return_is_flagged():
    found = _fabricated_status_returns(_s("""
        def _axiom_mqtt_publish_wrapper(client, topic, payload):
            client._run(client._client.publish(topic, payload))
            return 0
    """), [])
    assert len(found) == 1 and "`0`" in found[0]


# -------------------------------------------------------------- exemptions ---

def test_exception_as_status_is_the_correct_adapter():
    """D1's faithful wrapper: the library raises where the original returned."""
    assert _fabricated_status_returns(_s("""
        def _axiom_write_register_wrapper(master, unit, address, value):
            try:
                master.execute(unit, cst.WRITE_SINGLE_REGISTER, address,
                               output_value=value)
            except (ModbusError, OSError):
                return _WriteResult(error=True)
            return _WriteResult(error=False)
    """), []) == []


def test_returning_the_call_result_is_fine():
    assert _fabricated_status_returns(_s("""
        def publish(self, topic, payload):
            return self._run(self._client.publish(topic, payload))
    """), []) == []


def test_returning_a_value_derived_from_the_call_is_fine():
    assert _fabricated_status_returns(_s("""
        def read(master, unit, address, count):
            registers = master.execute(unit, cst.READ, address, count)
            return _RegisterReadResult(registers=list(registers), error=False)
    """), []) == []


def test_a_function_that_never_discards_a_call_is_quiet():
    assert _fabricated_status_returns(_s("""
        def build():
            return _Adapter(0)
    """), []) == []


def test_only_named_wrappers_are_inspected_when_names_are_given():
    src = _s("""
        def unrelated():
            some_call()
            return 0
    """)
    assert _fabricated_status_returns(src, ["a_wrapper"]) == []
    assert len(_fabricated_status_returns(src, [])) == 1


def test_unparseable_source_is_not_guessed_at():
    assert _fabricated_status_returns("def broken(:", []) == []


# ----------------------------------------- against the real archived clones ---

@pytest.mark.parametrize("path,flagged", [
    ("final_v4/proteus_full/B3_mqtt_telemetry_bridge_run3/bridge.py", True),
    ("final_v4/proteus_full/D3_telemetry_gateway_run1/sink.py", True),
    ("final_v4/round3/D1_boiler_control_loop_run1/modbus_shim.py", False),
])
def test_against_the_clones_this_corpus_produced(path, flagged):
    full = os.path.join(ROOT, path)
    if not os.path.isfile(full):
        pytest.skip(f"{path} not present")
    source = open(full, encoding="utf-8", errors="replace").read()
    assert bool(_fabricated_status_returns(source, [])) is flagged
