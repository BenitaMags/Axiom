#!/usr/bin/env python3
"""Falsify a judge finding by execution, or confirm it.

Why
───
15 of proteus_full's 19 MAJOR/BROKEN rows pass all seven deterministic layers:
the blind judge is the sole objector. Against 1 of 22 for single_shot. That is
structural -- the gate only ships code that cleared everything, so its bad
verdicts are the residue where nothing mechanical disagrees -- and it means the
arms are not judged on comparable populations.

Two of those 15 are already known to be judge errors::

    C2 r2  the two step() bodies differ only by nesting; `if A and B` is
           `if A: if B`. 41,160 calls, zero divergence.
    C1 r2  the reimplemented PID is bit-identical to real simple_pid across
           288 calls; the finding cites auto_mode/starting_output, API the
           program never touches.

Both were settled by running the code. This module does that systematically.

What it does
────────────
The recorded probes in ``equivalence/probes.py`` are happy-path: they fill a
window, publish two readings, poll a fake that always succeeds. Every finding
in the six regression cases lives on a path they never take -- a non-zero
result code, a connection that refuses, a double lacking an attribute, a 500
response. So each scenario here is ADVERSARIAL by construction, and compares
the original program with the clone on:

    the value returned          (including its type)
    the exception RAISED        (class name, as a caller's `except` sees it)
    what the injected double observed

An exception class is part of the comparison because that is what the findings
are about: ``requests.exceptions.HTTPError`` becoming ``httpx.HTTPStatusError``
is invisible to a check that only compares return values.

What a result means
───────────────────
DIVERGES  the original and the clone behave differently on this input. The
          judge's finding is CONFIRMED by execution.
MATCHES   they behave identically. The finding is not supported here -- for
          the input the finding itself describes.

This can go either way and is meant to. It is a measurement of the judge, not
a way to dismiss it: a scenario is written from the finding's own text, so a
finding that describes something real will show DIVERGES.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import traceback

CORPUS = os.path.expanduser("~/Downloads/evaluation")


# ── loading two copies of the same program side by side ────────────────────

def load(directory: str, tag: str, names: list[str]) -> dict:
    """Import a program's modules under a private prefix.

    Both the original and the clone define ``sink``, ``gateway`` and so on, so
    they cannot share ``sys.modules``. Each side is loaded under its own tag
    and the real names are aliased only while that side is executing, because
    the modules import each other by their plain names.
    """
    saved = {n: sys.modules.get(n) for n in names}
    sys.path.insert(0, directory)
    loaded: dict = {}
    try:
        for name in names:
            path = os.path.join(directory, f"{name}.py")
            if not os.path.isfile(path):
                continue
            spec = importlib.util.spec_from_file_location(f"{tag}_{name}", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"{tag}_{name}"] = module
            sys.modules[name] = module          # so siblings resolve
            spec.loader.exec_module(module)
            loaded[name] = module
    finally:
        sys.path.remove(directory)
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return loaded


def observe(fn) -> tuple:
    """Run ``fn`` and record what a caller would see: a value, or a raise."""
    try:
        value = fn()
    except BaseException as exc:                              # noqa: BLE001
        return ("raised", type(exc).__name__, str(exc)[:160])
    return ("returned", type(value).__name__, repr(value)[:160])


def observe_traced(fn) -> tuple:
    """``observe``, plus the set of function names the call actually executed.

    Without this the harness reproduces the exact blind spot the project exists
    to close. A scenario that INJECTS a double bypasses whatever the Connector
    wrote into ``_default_client``, so the migrated code never runs and both
    sides trivially agree -- which is how the corpus's own tests pass while
    shipping a broken adapter.

    Measured here: D3's Connector rewrote the CALL SITE, so an injected client
    still flows through the wrapper and the scenario reaches it. B3's rewrote
    only the default factory, so the same style of scenario never executes a
    single migrated line, and its MATCHES means nothing.
    """
    executed: set = set()

    def tracer(frame, event, arg):
        if event == "call":
            # Qualified by file: this harness's own doubles define `publish`
            # and `connect` too, and a bare name made an injected double's
            # method look like the clone's wrapper running.
            executed.add((os.path.abspath(frame.f_code.co_filename),
                          frame.f_code.co_name))
        return None

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        result = observe(fn)
    finally:
        sys.settrace(previous)
    return result, executed


# ── stubbing the library, so a clone's OWN factory can be exercised ────────
#
# Injecting a double bypasses `_default_client`, which is where the Connector
# puts its glue -- so the adapter never runs and both sides trivially agree.
# Stubbing the LIBRARY instead lets each side build its own default client and
# reach the code the finding is actually about.

import contextlib
import types


@contextlib.contextmanager
def stub_modules(mapping: dict):
    """Install fake library modules for the duration of a scenario."""
    saved = {name: sys.modules.get(name) for name in mapping}
    sys.modules.update(mapping)
    try:
        yield
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def _requests_stub(status=500):
    """The OUTGOING library, answering with a failing status."""
    mod = types.ModuleType("requests")
    exceptions = types.ModuleType("requests.exceptions")

    class RequestException(Exception):
        pass

    class HTTPError(RequestException):
        pass

    class ConnectionError(RequestException):      # noqa: A001
        pass

    class Timeout(RequestException):
        pass

    for cls in (RequestException, HTTPError, ConnectionError, Timeout):
        setattr(exceptions, cls.__name__, cls)

    class Response:
        status_code = status

        def json(self):
            return {"tag": "PT101", "value": 1.5, "quality": "GOOD"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise HTTPError(f"{self.status_code} Server Error")

    class Session:
        def get(self, url, timeout=None, **k):
            return Response()

        def post(self, url, json=None, timeout=None, **k):
            return Response()

    mod.exceptions = exceptions
    mod.Session = Session
    mod.Response = Response
    return {"requests": mod, "requests.exceptions": exceptions}


def _httpx_stub(status=500):
    """The INCOMING library, answering identically -- only the classes differ."""
    mod = types.ModuleType("httpx")

    class HTTPError(Exception):
        pass

    class HTTPStatusError(HTTPError):
        pass

    class ConnectError(HTTPError):
        pass

    class TimeoutException(HTTPError):
        pass

    class Response:
        status_code = status

        def json(self):
            return {"tag": "PT101", "value": 1.5, "quality": "GOOD"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise HTTPStatusError(f"{self.status_code} Server Error")

    class Client:
        def __init__(self, *a, **k):
            pass

        def get(self, url, timeout=None, **k):
            return Response()

        def post(self, url, json=None, timeout=None, **k):
            return Response()

        def close(self):
            pass

    class Timeout:
        def __init__(self, *a, **k):
            pass

    for name, obj in (("HTTPError", HTTPError), ("HTTPStatusError", HTTPStatusError),
                      ("ConnectError", ConnectError),
                      ("TimeoutException", TimeoutException),
                      ("Response", Response), ("Client", Client),
                      ("Timeout", Timeout)):
        setattr(mod, name, obj)
    return {"httpx": mod}


def _paho_stub(rc=7):
    """The OUTGOING MQTT library. publish() reports a REAL result code."""
    paho = types.ModuleType("paho")
    mqtt_pkg = types.ModuleType("paho.mqtt")
    client_mod = types.ModuleType("paho.mqtt.client")

    class MQTTMessageInfo:
        def __init__(self, rc):
            self.rc = rc

        def is_published(self):
            return True

        def wait_for_publish(self, timeout=None):
            return None

    class Client:
        def __init__(self, client_id=None, *a, **k):
            self.client_id = client_id

        def connect(self, host, port=1883, *a, **k):
            return 0

        def publish(self, topic, payload=None, qos=0, **k):
            return MQTTMessageInfo(rc)

        def loop_start(self):
            return None

        def disconnect(self):
            return 0

    client_mod.Client = Client
    client_mod.MQTTMessageInfo = MQTTMessageInfo
    mqtt_pkg.client = client_mod
    paho.mqtt = mqtt_pkg
    return {"paho": paho, "paho.mqtt": mqtt_pkg, "paho.mqtt.client": client_mod}


def _async_mqtt_stub(name):
    """An async MQTT library (amqtt / aiomqtt) whose publish succeeds silently.

    The point of the comparison: the outgoing library RETURNS a result code,
    this one returns nothing, and what the adapter does about that is the
    finding.
    """
    mod = types.ModuleType(name)

    class MqttError(Exception):
        pass

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            return 0

        async def publish(self, *a, **k):
            return None

        async def disconnect(self, *a, **k):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    mod.MqttError = MqttError
    mod.Client = _Client
    mod.MQTTClient = _Client
    sub = types.ModuleType(f"{name}.client")
    sub.MQTTClient = _Client
    sub.ClientException = MqttError
    mod.client = sub
    return {name: mod, f"{name}.client": sub}


def _pymodbus_stub(connect_ok=False):
    """The OUTGOING modbus library: connect() returns a BOOLEAN, never raises."""
    mod = types.ModuleType("pymodbus")
    client_mod = types.ModuleType("pymodbus.client")

    class ModbusTcpClient:
        def __init__(self, host, port=502, **k):
            self.host, self.port = host, port

        def connect(self):
            return connect_ok

        def read_holding_registers(self, address, count=1, **k):
            class R:
                registers = [0x4248, 0x0000, 0x1234, 0x5678][:max(count, 1)]

                def isError(self):
                    return False
            return R()

        def close(self):
            return None

    client_mod.ModbusTcpClient = ModbusTcpClient
    mod.client = client_mod
    return {"pymodbus": mod, "pymodbus.client": client_mod}


def _pymodbustcp_stub(open_ok=False):
    """pyModbusTCP: open() returns a boolean too, but the adapter may raise."""
    mod = types.ModuleType("pyModbusTCP")
    client_mod = types.ModuleType("pyModbusTCP.client")

    class ModbusClient:
        def __init__(self, host=None, port=502, unit_id=1, auto_open=False, **k):
            self.host, self.port = host, port

        def open(self):
            return open_ok

        def is_open(self):
            return open_ok

        def read_holding_registers(self, address, count=1):
            return [0x4248, 0x0000, 0x1234, 0x5678][:max(count, 1)]

        def close(self):
            return None

    client_mod.ModbusClient = ModbusClient
    mod.client = client_mod
    return {"pyModbusTCP": mod, "pyModbusTCP.client": client_mod}


def _modbus_tk_stub(connect_ok=True):
    """modbus-tk: execute() returns a tuple and raises on failure."""
    root = types.ModuleType("modbus_tk")
    defines = types.ModuleType("modbus_tk.defines")
    exceptions = types.ModuleType("modbus_tk.exceptions")
    modbus_tcp = types.ModuleType("modbus_tk.modbus_tcp")
    modbus = types.ModuleType("modbus_tk.modbus")

    defines.READ_HOLDING_REGISTERS = 3
    defines.WRITE_SINGLE_REGISTER = 6

    class ModbusError(Exception):
        pass

    class ModbusInvalidResponseError(ModbusError):
        pass

    for mod in (exceptions, modbus):
        mod.ModbusError = ModbusError
        mod.ModbusInvalidResponseError = ModbusInvalidResponseError

    class TcpMaster:
        def __init__(self, host=None, port=502, **k):
            self.host, self.port = host, port

        def open(self):
            if not connect_ok:
                raise ModbusError("cannot open")
            return None

        def set_timeout(self, *a, **k):
            return None

        def execute(self, slave, function_code, address, quantity_or_value=1, **k):
            return tuple([0x4248, 0x0000, 0x1234, 0x5678][:max(quantity_or_value, 1)])

        def close(self):
            return None

    modbus_tcp.TcpMaster = TcpMaster
    root.defines = defines
    root.exceptions = exceptions
    root.modbus_tcp = modbus_tcp
    root.modbus = modbus
    return {"modbus_tk": root, "modbus_tk.defines": defines,
            "modbus_tk.exceptions": exceptions,
            "modbus_tk.modbus_tcp": modbus_tcp, "modbus_tk.modbus": modbus}


def _any_modbus_stub():
    """Every modbus candidate at once.

    The stub has to match the library the clone ACTUALLY adopted, and that
    varies per seed: B2 r1 chose modbus_tk while r2 and r3 chose pyModbusTCP.
    Stubbing only one left the other real, so the clone opened a socket and
    the scenario reported `ConnectionRefusedError` as a behavioural divergence
    -- the harness failing, dressed up as a result.
    """
    out = {}
    out.update(_pymodbustcp_stub(open_ok=True))
    out.update(_modbus_tk_stub(connect_ok=True))
    return out


def _smtplib_stub(fail=True):
    """The OUTGOING mail library: SMTP() connects EAGERLY and can fail there."""
    mod = types.ModuleType("smtplib")

    class SMTPException(OSError):
        pass

    class SMTP:
        def __init__(self, host="localhost", port=25, *a, **k):
            if fail:
                raise ConnectionRefusedError(f"cannot reach {host}:{port}")

        def sendmail(self, sender, recipients, message):
            return {}

        def quit(self):
            return None

    mod.SMTP = SMTP
    mod.SMTPException = SMTPException
    return {"smtplib": mod}


def _apprise_stub():
    mod = types.ModuleType("apprise")

    class Apprise:
        def __init__(self, *a, **k):
            self.urls = []

        def add(self, url, *a, **k):
            self.urls.append(url)
            return True

        def notify(self, *a, **k):
            return True

    mod.Apprise = Apprise
    mod.AppriseAsset = object
    return {"apprise": mod}


# ── adversarial doubles ────────────────────────────────────────────────────

class RcClient:
    """An MQTT double whose publish reports a REAL result code.

    The recorded fake always succeeds, so nothing in the suite ever observes a
    non-zero rc. B3 and D3 both return ``getattr(info, "rc", 0)`` from the
    library's result object; a wrapper that returns a constant cannot.
    """
    def __init__(self, rc=7):
        self.rc, self.published = rc, []

    def connect(self, *a, **k):
        return 0

    def publish(self, topic, payload=None, **k):
        self.published.append((topic, payload))
        rc = self.rc
        return type("Info", (), {"rc": rc, "is_published": lambda self: True})()

    def loop_start(self):
        return None

    def disconnect(self):
        return 0


class NoRcClient(RcClient):
    """A double whose publish returns an object with NO ``.rc`` at all.

    The original's ``getattr(info, "rc", 0)`` tolerates this and yields 0.
    """
    def publish(self, topic, payload=None, **k):
        self.published.append((topic, payload))
        return {"queued": True}


class FailingSession:
    """An HTTP double that answers 500, so ``raise_for_status`` must raise."""
    def __init__(self, status=500):
        self.status = status

    def _resp(self):
        status = self.status

        class R:
            status_code = status

            def json(self):
                return {"demand": 1.0, "runtimes": [1, 2, 3]}

            def raise_for_status(self):
                import requests
                raise requests.exceptions.HTTPError(f"{status} Server Error")
        return R()

    def get(self, url, timeout=None, **k):
        return self._resp()

    def post(self, url, json=None, timeout=None, **k):
        return self._resp()


# ── scenarios, each written from one finding's own text ────────────────────
#
# Keyed by program. Each entry is (finding, module-list, scenario) where the
# scenario receives the loaded modules and returns what a caller observes.

def _d3_nonzero_rc(m):
    sink = m["sink"].MqttSink(client=RcClient(rc=7))
    return sink.publish("A", {"v": 1})


def _d3_missing_rc(m):
    sink = m["sink"].MqttSink(client=NoRcClient())
    return sink.publish("A", {"v": 1})


READING = {"value": 42.5, "ts": 1700000000, "quality": "GOOD"}


def _b3(m, client):
    cfg = m["mqtt_config"].BridgeConfig()
    bridge = m["bridge"].TelemetryBridge(cfg, client=client)
    return bridge.publish_reading("A", READING)


def _b3_nonzero_rc(m):
    return _b3(m, RcClient(rc=7))


def _b3_missing_rc(m):
    return _b3(m, NoRcClient())


def _b1_http_error(m):
    poller = m["scada_poller"].ScadaPoller("http://x", session=FailingSession(500))
    return poller.read_tag("PT101")


def _b1_default_client_500(m):
    """No injection: let each side build its OWN client, then ask for a tag.

    This is the path the finding is about -- `_default_session()`, where the
    Connector put its glue. The libraries are stubbed to answer identically
    (500), so the only thing that can differ is the exception class the caller
    sees.
    """
    return m["scada_poller"].ScadaPoller("http://x").read_tag("PT101")


def _b1_poll_http_error(m):
    poller = m["scada_poller"].ScadaPoller("http://x", session=FailingSession(500))
    return poller.poll(["PT101", "PT102"])


def _c2_interlock_in_fault(m):
    c = m["fsm"].TankController()
    seen = []
    for level in (0.5, 9.0, 12.0, 99.0, 0.5, 4.0):
        c.step(level)
        seen.append((c.state, c.inlet, c.outlet))
    return seen


def _c1_pid_sequence(m):
    """C1: the pump command sequence the program actually produces.

    The sleep is load-bearing, not politeness. simple_pid has
    ``sample_time=0.01`` and short-circuits to ``_last_output`` for any call
    that arrives sooner, and the program calls ``self.pid(level)`` with no
    explicit dt. Without a pause this loop runs far inside that window, every
    step returns the same cached number, and the scenario compared a constant
    against a constant -- reporting MATCHES while measuring nothing. In
    production these calls are seconds apart.
    """
    import time as _time

    ctrl = m["controller"].PumpController(setpoint=8.0)
    out = []
    for level in (0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 0.0):
        out.append(ctrl.update(level))
        _time.sleep(0.02)
    return out


def _a2_non_ascii(m):
    """A2 r2: ujson emits non-ASCII raw; stdlib json escapes it as \\uXXXX."""
    mod = m["sensor_json_logger"]
    return [mod.format_reading("TAG\u00e9", 1.5, "2024-01-01T00:00:00Z", "G\u00d6OD"),
            mod.format_reading("caf\u00e9", "na\u00efve", "2024-01-01T00:00:00Z"),
            mod.format_batch([{"tag": "\u00fc", "value": 1, "ts": "t",
                               "quality": "\u00e9"}])]


class _Blocked:
    """An import hook that makes chosen packages unavailable."""

    def __init__(self, names):
        self.names = set(names)

    def find_module(self, fullname, path=None):
        return self if fullname.split(".")[0] in self.names else None

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise ImportError(f"No module named {fullname!r}")
        return None


def importable_without(directory: str, names: list, blocked: list) -> tuple:
    """Can these modules be imported while ``blocked`` packages are missing?

    This is the C3 question and it is import-time, so nothing is called and no
    function runs -- the NOT-REACHED guard is not meaningful here, which is why
    this scenario is marked ``import_time``.
    """
    hook = _Blocked(blocked)
    sys.meta_path.insert(0, hook)
    try:
        load(directory, "probe", names)
        return ("returned", "bool", "True -- imports with the backend absent")
    except BaseException as exc:                              # noqa: BLE001
        return ("raised", type(exc).__name__, str(exc)[:160])
    finally:
        sys.meta_path.remove(hook)


def _c3_injected_smtp(m):
    """C3: construct with an injected transport, as the tests do."""
    sent = []

    class Smtp:
        def sendmail(self, sender, recipients, message):
            sent.append((sender, tuple(recipients), message))
            return {}

    n = m["notifier"].EmailNotifier("a@b", ["c@d"], smtp=Smtp())
    out = n.notify("subject", "body")
    return (out, sent)


def _b3_default_client_publish(m):
    """B3 r1/r3: no injection -- each side builds its own client and publishes."""
    cfg = m["mqtt_config"].BridgeConfig()
    bridge = m["bridge"].TelemetryBridge(cfg)
    return bridge.publish_reading("A", READING)


def _d3_default_client_publish(m):
    """D3 r2: same, through MqttSink."""
    return m["sink"].MqttSink().publish("A", {"v": 1})


def _b2_failed_connect(m):
    """B2 r2: the original ignored connect()'s False and returned the client.

    The comparison is deliberately BEHAVIOURAL, not structural. After a
    library swap the returned object is a different class by construction, so
    comparing its type or repr reports a divergence on every correct migration
    -- the harness's own version of the mistake it exists to catch. What the
    finding actually claims is that the rewrite RAISES where the original
    returned, so that is what is compared.
    """
    mod = m["modbus_meter_reader"]
    factory = getattr(mod, "_default_client", None)
    if factory is None:
        raise AttributeError("no _default_client")
    client = factory("10.0.0.1")
    return ("returned a client",
            hasattr(client, "read_holding_registers") or hasattr(client, "read"))


def _c3_eager_connection(m):
    """C3 r2: the original opened a real SMTP connection at construction."""
    return m["notifier"].EmailNotifier("a@b", ["c@d"])


def _d2_default_client_500(m):
    """D2: default session, 500 response -- which exception class escapes?"""
    return m["client"].ScadaClient("http://x").get_demand()


class _QosStrictClient(RcClient):
    """paho validates QoS and raises; an adapter that silently downgrades does not."""
    def publish(self, topic, payload=None, qos=0, **k):
        if qos not in (0, 1, 2):
            raise ValueError("Invalid QoS level: " + str(qos))
        return super().publish(topic, payload, qos=qos, **k)


def _b3_invalid_qos(m):
    cfg = m["mqtt_config"].BridgeConfig()
    cfg.qos = 3
    bridge = m["bridge"].TelemetryBridge(cfg, client=_QosStrictClient())
    return bridge.publish_reading("A", READING)


class _ErroringModbus:
    """A device that answers with a Modbus exception response.

    pymodbus reported that through `rr.isError()`, which the program turned
    into `IOError("Modbus read failed @ ...")`.
    """
    def connect(self):
        return True

    def read_holding_registers(self, address, count=1, **k):
        class R:
            registers = []

            def isError(self):
                return True
        return R()

    def write_register(self, address, value, **k):
        class R:
            def isError(self):
                return True
        return R()


def _d1_device_error(m):
    io = m["transport"].ModbusIO(client=_ErroringModbus())
    return io.read_temperature()


# ── broad behavioural sweeps, for rows the judge rated CLEAN ──────────────
#
# A CLEAN verdict names no defect, so there is no claim to test. The question
# becomes the opposite one: is there ANY divergence the judge missed? These
# sweep the program's public API over ordinary and boundary inputs and compare
# everything observable, including the exception raised.

def _a1_crc_sweep(m):
    mod = m["crc_telemetry"]
    out = []
    payloads = [b"", b"\x00", b"A", b"hello world", bytes(range(256)),
                b"\xff" * 64, b"\x00" * 1024]
    for payload in payloads:
        frame = mod.build_frame(payload)
        out.append((mod.crc32(payload), frame.hex(), mod.verify_frame(frame),
                    mod.extract_payload(frame)))
    # corrupted frames, truncated frames, and a frame too short to hold a CRC
    good = mod.build_frame(b"telemetry")
    for bad in (good[:-1], good[:2], b"", good[:-4] + b"\x00\x00\x00\x00",
                bytes([good[0] ^ 0xFF]) + good[1:]):
        out.append(observe(lambda f=bad: mod.verify_frame(f)))
        out.append(observe(lambda f=bad: mod.extract_payload(f)))
    return out


def _a3_timestamp_sweep(m):
    mod = m["iso_ts_parser"]
    stamps = [
        "2024-01-01T00:00:00Z", "2024-01-01T00:00:00+00:00",
        "2024-06-30T23:59:59.999999Z", "2024-02-29T12:00:00Z",
        "1970-01-01T00:00:00Z", "2038-01-19T03:14:07Z",
        "2024-01-01T00:00:00.5Z", "2024-01-01T00:00:00-05:30",
        "2024-12-31T23:59:60Z",              # leap second
        "not a timestamp", "", "2024-13-01T00:00:00Z", "2024-01-01",
    ]
    out = []
    for ts in stamps:
        out.append(observe(lambda t=ts: str(mod.parse(t))))
        out.append(observe(lambda t=ts: mod.to_epoch(t)))
    return out


class _MeterClient:
    """A Modbus double returning fixed register values, and then an error."""
    def __init__(self, error=False):
        self.error = error

    def connect(self):
        return True

    def read_holding_registers(self, address, count=1, **k):
        error = self.error

        class R:
            registers = [0x4248, 0x0000, 0x1234, 0x5678][:max(count, 1)]

            def isError(self):
                return error
        return R()

    def close(self):
        return None


def _b2_default_client_measurement(m):
    """B2 through its OWN factory, so the migrated code actually runs."""
    return m["modbus_meter_reader"].MeterReader(host="10.0.0.1").read_measurement()


def _b2_measurement(m):
    reader = m["modbus_meter_reader"].MeterReader(client=_MeterClient())
    ok = observe(lambda: reader.read_measurement())
    failing = m["modbus_meter_reader"].MeterReader(client=_MeterClient(error=True))
    bad = observe(lambda: failing.read_measurement())
    decode = m["registers"].decode
    decoded = [observe(lambda e=enc, w=words: decode(e, w, 0.1))
               for enc, words in (("u16", [1234]), ("u32", [1, 2]),
                                  ("s16", [0xFFFF]), ("f32", [0x4248, 0x0000]),
                                  ("u16", []), ("nope", [1]))]
    return (ok, bad, decoded)


SCENARIOS: dict = {
    "D3_telemetry_gateway": [
        ("D3 r1/r2: the wrapper returns a constant, so a non-zero rc is unreachable",
         ["estimator", "gateway", "sink"], _d3_nonzero_rc),
        ("D3 r1/r2: a double with no .rc -- the original's getattr default was 0",
         ["estimator", "gateway", "sink"], _d3_missing_rc),
        ("D3 r2: DEFAULT client -- can a non-zero rc still reach the caller?",
         ["estimator", "gateway", "sink"], _d3_default_client_publish,
         lambda: _paho_stub(rc=7), lambda: _async_mqtt_stub("aiomqtt")),
    ],
    "B3_mqtt_telemetry_bridge": [
        ("B3 r1/r3: the wrapper always reports rc=0",
         ["telemetry", "mqtt_config", "bridge"], _b3_nonzero_rc),
        ("B3 r1/r3: a double with no .rc",
         ["telemetry", "mqtt_config", "bridge"], _b3_missing_rc),
        ("B3 r3: an out-of-range qos -- is it validated or silently downgraded?",
         ["telemetry", "mqtt_config", "bridge"], _b3_invalid_qos),
        ("B3 r1/r3: DEFAULT client -- can a non-zero rc still reach the caller?",
         ["telemetry", "mqtt_config", "bridge"], _b3_default_client_publish,
         lambda: _paho_stub(rc=7), lambda: _async_mqtt_stub("amqtt")),
        ("B3 r2: DEFAULT client -- can a non-zero rc still reach the caller?",
         ["telemetry", "mqtt_config", "bridge"], _b3_default_client_publish,
         lambda: _paho_stub(rc=7), lambda: _async_mqtt_stub("aiomqtt")),
    ],
    "A1_crc_telemetry": [
        ("A1: CRC frames, round trips, corruption and truncation",
         ["crc_telemetry"], _a1_crc_sweep),
    ],
    "A3_iso_ts_parser": [
        ("A3: timestamps, offsets, boundaries and malformed input",
         ["iso_ts_parser"], _a3_timestamp_sweep),
    ],
    "B2_modbus_meter_reader": [
        ("B2: a measurement, a device error, and register decoding",
         ["registers", "modbus_meter_reader"], _b2_measurement),
        ("B2: a measurement through the DEFAULT client",
         ["registers", "modbus_meter_reader"], _b2_default_client_measurement,
         lambda: _pymodbus_stub(connect_ok=True), _any_modbus_stub),
        ("B2 r2: connect() fails -- does the original's client still come back?",
         ["modbus_meter_reader"], _b2_failed_connect,
         lambda: _pymodbus_stub(connect_ok=False), _any_modbus_stub),
    ],
    "B1_scada_poller": [
        ("B1 r1: a 500 response -- which exception class reaches the caller?",
         ["scada_poller"], _b1_http_error),
        ("B1 r1: the same through poll()",
         ["scada_poller"], _b1_poll_http_error),
        ("B1 r1/r3: DEFAULT client, 500 response -- which exception class escapes?",
         ["scada_poller"], _b1_default_client_500,
         _requests_stub, _httpx_stub),
    ],
    "D1_boiler_control_loop": [
        ("D1 r1: the device answers with a Modbus exception response",
         ["transport"], _d1_device_error),
    ],
    "C2_tank_level_fsm": [
        ("C2 r1/r2/r3: the interlock is violated while already in FAULT",
         ["alarms", "fsm"], _c2_interlock_in_fault),
    ],
    "A2_sensor_json_logger": [
        ("A2 r2: non-ASCII -- stdlib json escapes, ujson emits raw",
         ["sensor_json_logger"], _a2_non_ascii),
    ],
    "C3_anomaly_threshold_engine": [
        ("C3 r1/r3: can the module still be imported with the backend absent?",
         ["hysteresis", "engine_config", "notifier"], "IMPORT_TIME"),
        ("C3: notify() through an injected transport",
         ["hysteresis", "engine_config", "notifier"], _c3_injected_smtp),
        ("C3 r2: DEFAULT transport -- does construction still fail eagerly?",
         ["hysteresis", "engine_config", "notifier"], _c3_eager_connection,
         lambda: _smtplib_stub(fail=True), _apprise_stub),
    ],
    "D2_pump_station_scheduler": [
        ("D2 r2/r3: DEFAULT client, 500 -- which exception class escapes?",
         ["client"], _d2_default_client_500, _requests_stub, _httpx_stub),
    ],
    "C1_pid_pump_controller": [
        ("C1 r1/r2/r3: the PID output sequence the program actually produces",
         ["io_map", "controller"], _c1_pid_sequence),
    ],
}


def _clone_defined_names(clone_dir: str) -> set:
    """Every function/method name defined in the clone's own modules."""
    import ast as _ast
    found: set = set()
    for name in sorted(os.listdir(clone_dir)):
        if not name.endswith(".py") or name.startswith("test_"):
            continue
        try:
            tree = _ast.parse(open(os.path.join(clone_dir, name),
                                   encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                found.add(node.name)
    return found


#: Exceptions that mean the SCENARIO is wrong, not the clone. Both sides raise
#: them identically, which would score a MATCHES that proves nothing.
_HARNESS_MISUSE = {"TypeError", "KeyError", "AttributeError", "NameError",
                   "ImportError", "ModuleNotFoundError"}

#: A real socket was opened, which means a stub did not take effect and the
#: genuine library ran. Measured on B2 seed 1, which adopted modbus_tk while
#: the scenario stubbed pyModbusTCP: the clone reached the network and
#: `ConnectionRefusedError` was reported as a behavioural divergence.
_REACHED_THE_NETWORK = {"ConnectionRefusedError", "ConnectionError", "OSError",
                        "socket.timeout", "TimeoutError", "gaierror"}


def applies_to(finding: str, run_id: str) -> bool:
    """Does this scenario test the finding recorded for THIS run?

    Scenarios are keyed by program, but different seeds of the same program
    got different findings. C3 r1 and r3 were rated MAJOR for a hoisted lazy
    import; r2 was rated MAJOR for something else entirely (the original
    opened a real SMTP connection eagerly). Scoring r2 with r1's scenario
    would report a false positive on a claim that was never tested -- which is
    the same error as scoring a clone with a scenario that never reached it.

    The run numbers are read off the label, e.g. "C3 r1/r3: ...".
    """
    import re as _re
    head = finding.split(":", 1)[0]
    runs = _re.findall(r"r(\d+)", head)
    return (not runs) or (str(run_id) in runs)


def run(program: str, clone_dir: str, corpus: str = CORPUS,
        run_id: str = "") -> list:
    original_dir = os.path.join(corpus, program)
    results = []
    for entry in SCENARIOS.get(program, []):
        # Either (finding, modules, scenario) or, when each side needs its own
        # library stubbed, (finding, modules, scenario, orig_stubs, clone_stubs).
        finding, names, scenario = entry[0], entry[1], entry[2]
        if run_id and not applies_to(finding, run_id):
            continue
        orig_stubs = entry[3] if len(entry) > 3 else (lambda: {})
        clone_stubs = entry[4] if len(entry) > 4 else (lambda: {})
        if scenario == "IMPORT_TIME":
            blocked = ["smtplib", "apprise", "redmail", "yagmail", "paho",
                       "aiomqtt", "amqtt", "gmqtt", "httpx", "requests",
                       "pymodbus", "minimalmodbus", "modbus_tk", "ujson",
                       "orjson", "simple_pid", "transitions", "ciso8601",
                       "fastcrc"]
            a = importable_without(original_dir, names, blocked)
            b = importable_without(clone_dir, names, blocked)
            results.append((finding, "MATCHES" if a == b else "DIVERGES",
                            f"original {a}\n        clone    {b}"))
            continue
        try:
            with stub_modules(orig_stubs()):
                before = load(original_dir, "orig", names)
                a = observe(lambda: scenario(before))
        except BaseException as exc:                          # noqa: BLE001
            results.append((finding, "SKIPPED",
                            f"original would not load/run: {type(exc).__name__}: {exc}"))
            continue
        try:
            with stub_modules(clone_stubs()):
                after = load(clone_dir, "clone", names)
                b, executed = observe_traced(lambda: scenario(after))
        except BaseException as exc:                          # noqa: BLE001
            results.append((finding, "SKIPPED",
                            f"clone would not load/run: {type(exc).__name__}: {exc}"))
            continue

        # Did the scenario actually run any migrated code? "Migrated" is
        # defined structurally: a function the clone defines and the original
        # does not. Matching on an `_axiom`/`_spt` prefix was too loose --
        # B3's fabricating wrapper is a method plainly called `publish`, so a
        # stray `_spt_` helper elsewhere made an untested scenario look
        # reached.
        migrated_names = (_clone_defined_names(clone_dir)
                          - _clone_defined_names(original_dir))
        clone_root = os.path.abspath(clone_dir)
        ran_in_clone = {name for path, name in executed
                        if path.startswith(clone_root)}
        if migrated_names and not (ran_in_clone & migrated_names):
            results.append((finding, "NOT-REACHED",
                            f"no migrated code ran (injected double bypassed "
                            f"the adapter); the clone defines "
                            f"{len(migrated_names)} migrated function(s), none "
                            f"executed"))
            continue
        # A scenario that misuses the API raises the SAME TypeError/KeyError
        # on both sides and would otherwise score a meaningless MATCHES. That
        # is the harness failing, not evidence about the clone, so it is
        # reported as INVALID and excluded from the tally.
        if any(o[0] == "raised" and o[1] in _REACHED_THE_NETWORK
               for o in (a, b)):
            results.append((finding, "INVALID",
                            f"a stub did not take effect and the real library "
                            f"opened a socket: original {a[1]}, clone {b[1]}"))
            continue
        if a == b and a[0] == "raised" and a[1] in _HARNESS_MISUSE:
            results.append((finding, "INVALID",
                            f"the scenario never reached the code under test: "
                            f"{a[1]}: {a[2]}"))
            continue
        verdict = "MATCHES" if a == b else "DIVERGES"
        results.append((finding, verdict, f"original {a}\n        clone    {b}"))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--clones", default="final_v4")
    ap.add_argument("--config", default="proteus_full")
    ap.add_argument("--case", action="append",
                    help="PROGRAM:RUN (repeatable); default is the six regressions")
    args = ap.parse_args(argv)

    # The 15 rows where every deterministic layer passed and only the judge
    # objected -- the population where a false positive is possible at all.
    cases = args.case or [
        "A2_sensor_json_logger:2", "B1_scada_poller:1",
        "B2_modbus_meter_reader:2",
        "B3_mqtt_telemetry_bridge:1", "B3_mqtt_telemetry_bridge:2",
        "B3_mqtt_telemetry_bridge:3", "C1_pid_pump_controller:2",
        "C2_tank_level_fsm:1", "C2_tank_level_fsm:2", "C2_tank_level_fsm:3",
        "C3_anomaly_threshold_engine:1", "C3_anomaly_threshold_engine:2",
        "C3_anomaly_threshold_engine:3",
        "D3_telemetry_gateway:1", "D3_telemetry_gateway:2",
    ]
    tally = {"DIVERGES": 0, "MATCHES": 0, "SKIPPED": 0, "INVALID": 0,
             "NOT-REACHED": 0}
    for case in cases:
        program, _, run_id = case.partition(":")
        clone = os.path.join(args.clones, args.config, f"{program}_run{run_id}")
        print("=" * 78)
        print(f"{program}  run{run_id}")
        if not os.path.isdir(clone):
            print(f"  no clone at {clone}")
            continue
        outcome = run(program, clone, args.corpus, run_id)
        if not outcome:
            print("  no scenario written for this program")
        for finding, verdict, detail in outcome:
            tally[verdict] = tally.get(verdict, 0) + 1
            print(f"  [{verdict:8s}] {finding}")
            for line in detail.splitlines():
                print(f"        {line.strip() if line.startswith('        ') else line}")
    print("=" * 78)
    print(f"DIVERGES (finding confirmed by execution): {tally['DIVERGES']}")
    print(f"MATCHES  (finding not supported here)    : {tally['MATCHES']}")
    print(f"SKIPPED                                  : {tally['SKIPPED']}")
    print(f"INVALID  (scenario never reached the code): {tally['INVALID']}")
    print(f"NOT-REACHED (injected double bypassed it): {tally['NOT-REACHED']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
