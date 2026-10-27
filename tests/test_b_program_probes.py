"""B1-B3 had no probes, and their blind spot is dependency injection.

All three wrap a `_default_*` factory that builds the real library's client, and
every corpus test injects a fake -- so the factory, the ONLY code the migration
rewrites, is never executed. That is the same blind spot C1 and C2 had, and the
opposite of category A, whose tests do call the real library.

So the probes come in two halves: deterministic fakes that pin the normalisation,
encoding and error handling around the client, and -- for B1 -- a real local HTTP
server that executes the factory.

Every subject has a passing control as well as failing ones. An oracle that
rejects everything satisfies the negative tests and measures nothing.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "equivalence"))

CORPUS = os.path.expanduser("~/Downloads/evaluation")
pytestmark = pytest.mark.skipif(
    not os.path.isdir(CORPUS), reason="evaluation corpus not present")


def _oracle(name):
    from oracle import ProgramOracle
    return ProgramOracle(os.path.join(CORPUS, name), name)


def _src(program, module):
    return open(os.path.join(CORPUS, program, f"{module}.py"),
                encoding="utf-8").read()


def _swap(text, old, new):
    assert old in text, f"fixture pattern no longer matches: {old!r}"
    out = text.replace(old, new)
    assert out != text
    return out


# ── B1: requests -> httpx, with a live server ────────────────────────────────

_REQUESTS_FACTORY = ("    import requests  # imported lazily so tests can inject"
                     " a fake session\n\n    return requests.Session()")
_HTTPX_FACTORY = ("    import httpx  # imported lazily so tests can inject a "
                  "fake session\n\n    return httpx.Client()")


def _b1_httpx():
    return _swap(_src("B1_scada_poller", "scada_poller"),
                 _REQUESTS_FACTORY, _HTTPX_FACTORY)


def test_b1_accepts_the_httpx_migration():
    """The live half: `httpx.Client()` is exercised against a real server.

    This is what the corpus cannot check -- its test injects a FakeSession, so
    `_default_session()` never runs and the HTTP library is never touched.
    """
    pytest.importorskip("httpx")
    r = _oracle("B1_scada_poller").check_many({"scada_poller": _b1_httpx()})
    assert r.equivalent, r.detail


def test_b1_rejects_a_dropped_timeout():
    """Without a timeout a dead gateway hangs the poller forever."""
    pytest.importorskip("httpx")
    src = _swap(_b1_httpx(),
                'resp = self.session.get(f"{self.base_url}/tags/{tag}", '
                'timeout=self.timeout)',
                'resp = self.session.get(f"{self.base_url}/tags/{tag}")')
    r = _oracle("B1_scada_poller").check_many({"scada_poller": src})
    assert not r.equivalent


def test_b1_rejects_a_swallowed_http_error():
    pytest.importorskip("httpx")
    src = _swap(_b1_httpx(), "        resp.raise_for_status()\n", "")
    r = _oracle("B1_scada_poller").check_many({"scada_poller": src})
    assert not r.equivalent
    assert "returned a record instead of raising" in r.detail


def test_b1_rejects_a_changed_quality_default():
    pytest.importorskip("httpx")
    src = _swap(_b1_httpx(), '"quality": data.get("quality", "GOOD"),',
                '"quality": data.get("quality", "UNKNOWN"),')
    r = _oracle("B1_scada_poller").check_many({"scada_poller": src})
    assert not r.equivalent


def test_b1_error_probes_do_not_pin_the_exception_class():
    """`requests` raises HTTPError, `httpx` raises HTTPStatusError.

    A clone cannot raise the outgoing library's class without importing the
    library it was told to replace, so demanding the type would make the subject
    unmigratable -- the mistake the judge rubric made. The probe records whether
    a failed request raised, not what it raised.
    """
    import json as _json
    golden = _json.load(open(os.path.join(ROOT, "equivalence", "golden.json"),
                             encoding="utf-8"))["B1_scada_poller"]
    assert all(row[1] == "raised" for row in golden["http_errors"])
    assert golden["live_404"] == "raised"


def test_b1_live_probe_leaks_no_port_into_the_golden_vector():
    """The server binds an ephemeral port; recording it would break every run."""
    import json as _json
    blob = _json.dumps(_json.load(
        open(os.path.join(ROOT, "equivalence", "golden.json"),
             encoding="utf-8"))["B1_scada_poller"])
    assert "127.0.0.1" not in blob
    # ...but the live records themselves must be there, or the probe silently
    # stopped exercising the factory and nobody would notice.
    assert "live_records" in blob and "PT101" in blob


# ── B2: the glue around an injected Modbus client ────────────────────────────

def _b2_sources(reader=None, registers=None):
    return {"modbus_meter_reader": reader if reader is not None
            else _src("B2_modbus_meter_reader", "modbus_meter_reader"),
            "registers": registers if registers is not None
            else _src("B2_modbus_meter_reader", "registers")}


def test_b2_accepts_the_unchanged_program():
    r = _oracle("B2_modbus_meter_reader").check_many(_b2_sources())
    assert r.equivalent, r.detail


def test_b2_rejects_a_lost_unit_id():
    """Reading the wrong slave returns plausible numbers from the wrong meter."""
    src = _swap(_src("B2_modbus_meter_reader", "modbus_meter_reader"),
                "result = self.client.read_holding_registers(address, count, "
                "unit=self.unit)",
                "result = self.client.read_holding_registers(address, count)")
    r = _oracle("B2_modbus_meter_reader").check_many(_b2_sources(reader=src))
    assert not r.equivalent
    assert "calls" in r.detail


def test_b2_rejects_a_swallowed_modbus_error():
    src = _swap(_src("B2_modbus_meter_reader", "modbus_meter_reader"),
                '            if result.isError():\n'
                '                raise IOError(f"Modbus read failed for {name}")\n',
                '')
    r = _oracle("B2_modbus_meter_reader").check_many(_b2_sources(reader=src))
    assert not r.equivalent


def test_b2_rejects_little_endian_decoding():
    """Modbus words are big-endian; the byte order is the whole decode."""
    src = _swap(_src("B2_modbus_meter_reader", "registers"),
                'value = struct.unpack(">f", raw)[0]',
                'value = struct.unpack("<f", raw)[0]')
    r = _oracle("B2_modbus_meter_reader").check_many(_b2_sources(registers=src))
    assert not r.equivalent
    assert "decode_f32" in r.detail or "measurement" in r.detail


def test_minimalmodbus_cannot_serve_b2s_transport():
    """The corpus names a target that cannot do the job.

    B2 asks for `pymodbus.ModbusTcpClient(host, port=502)` -> `minimalmodbus`,
    and `minimalmodbus.Instrument(port, slaveaddress, mode='rtu'|'ascii')` speaks
    Modbus over a SERIAL port only -- there is no TCP entry point. A faithful
    migration to the declared target is therefore impossible, which is a property
    of the corpus rather than of any arm, and it explains why every B2 clone
    adopted some other library.
    """
    minimalmodbus = pytest.importorskip("minimalmodbus")
    assert not [n for n in dir(minimalmodbus) if "tcp" in n.lower()]
    import inspect
    params = inspect.signature(minimalmodbus.Instrument.__init__).parameters
    assert "port" in params and "host" not in params


# ── B3: the glue around an injected MQTT client ──────────────────────────────

def _b3_sources(**overrides):
    sources = {name: _src("B3_mqtt_telemetry_bridge", name)
               for name in ("bridge", "telemetry", "mqtt_config")}
    sources.update(overrides)
    return sources


def test_b3_accepts_the_unchanged_program():
    r = _oracle("B3_mqtt_telemetry_bridge").check_many(_b3_sources())
    assert r.equivalent, r.detail


def test_b3_rejects_a_dropped_qos():
    """QoS changes the delivery guarantee, and the returned record reports
    `cfg.qos` rather than what was passed -- so nothing else would show it."""
    src = _swap(_src("B3_mqtt_telemetry_bridge", "bridge"),
                "info = self.client.publish(topic, payload, qos=self.cfg.qos)",
                "info = self.client.publish(topic, payload)")
    r = _oracle("B3_mqtt_telemetry_bridge").check_many(_b3_sources(bridge=src))
    assert not r.equivalent
    assert "client_calls" in r.detail


def test_b3_rejects_an_extra_payload_field():
    """Extra fields leak onto the wire and change every message's bytes."""
    src = _swap(_src("B3_mqtt_telemetry_bridge", "telemetry"),
                '        "q": reading.get("quality", "GOOD"),',
                '        "q": reading.get("quality", "GOOD"),\n'
                '        "tag": reading.get("tag"),')
    r = _oracle("B3_mqtt_telemetry_bridge").check_many(_b3_sources(telemetry=src))
    assert not r.equivalent


def test_b3_rejects_a_str_payload():
    """MQTT payloads are bytes."""
    src = _swap(_src("B3_mqtt_telemetry_bridge", "telemetry"),
                'return json.dumps(body, separators=(",", ":")).encode("utf-8")',
                'return json.dumps(body, separators=(",", ":"))')
    r = _oracle("B3_mqtt_telemetry_bridge").check_many(_b3_sources(telemetry=src))
    assert not r.equivalent


def test_b3_has_no_live_broker_probe_yet():
    """Record the gap rather than implying it is covered.

    Without a broker, an async client whose `publish` returns an un-awaited
    coroutine publishes NOTHING while `getattr(info, "rc", 0)` still yields 0 --
    a silent success. The injected-fake probes cannot see it, because they inject
    a synchronous fake. B1's live server closes this for HTTP; the MQTT and
    Modbus equivalents are not built.
    """
    import equivalence.probes as P
    source = open(P.__file__, encoding="utf-8").read()
    assert "_serve_fixed_json" in source
    assert "live_records" in source
    # If a live broker probe is added, update this test rather than deleting it.
    assert "_serve_mqtt_broker" not in source


# ── all twelve programs now have vectors ─────────────────────────────────────

def test_every_program_has_probes_and_invariants():
    import equivalence.probes as P
    import smart_loader.eval.proteus_harness as H
    names = {p.name for p in H.discover_programs(CORPUS)}
    assert names <= set(P.PROBES), sorted(names - set(P.PROBES))
    assert names <= set(P.INVARIANTS), sorted(names - set(P.INVARIANTS))


@pytest.mark.parametrize("program", ["B1_scada_poller", "B2_modbus_meter_reader",
                                     "B3_mqtt_telemetry_bridge"])
def test_the_pristine_original_passes_every_layer(program):
    import smart_loader.eval.proteus_harness as H
    prog = {p.name: p for p in H.discover_programs(CORPUS)}[program]
    verdict, detail = H.oracle_verdict(prog, prog.path)
    assert verdict == "Y", detail
