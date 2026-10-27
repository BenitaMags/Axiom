"""Deterministic probes and invariants for the six C/D evaluation programs.

This is the same idea as ``ics_corpus/oracle.py``, generalised so one module
covers several programs instead of each program shipping its own harness.

Why it exists
─────────────
The Logic Substitution Agent refuses to emit a rewritten program it cannot
prove equivalent, and its proof needs recorded correct answers. The ICS corpus
ships those (``golden.json`` plus 20 branch-covering probes); this corpus ships
ordinary pytest suites instead. So on every run the agent said "no equivalence
corpus configured" and did nothing — meaning categories C and D, whose
specification asks for *"diversify all first-party logic"*, were never
diversified at all.

A pytest suite alone is a weaker guarantee: it answers "did anything break",
not "is the output identical". These probes record actual values, so a rewrite
that changes a number the tests never assert on is still caught.

Design rules
────────────
*Deterministic.* No clocks, no RNG without a fixed seed, no network. Every
external dependency in this corpus is injected, so the fakes below stand in for
it — the same trick the corpus's own tests use.

*Branch-covering.* Probes are chosen to reach each state and each guard, not to
be representative. Coverage is what makes the recorded values meaningful: a
golden set that never enters the FAULT path cannot detect a broken FAULT path.

*JSON-able.* Outputs are compared after a round-trip through JSON, so floats
keep full precision and ordering is stable.
"""

from __future__ import annotations

import time


# ── injected fakes (mirroring what each program's own tests use) ──────────────

class FakePID:
    """Proportional stand-in for ``simple_pid.PID``."""

    def __init__(self, kp: float = 1.0) -> None:
        self.kp = kp
        self.setpoint = 0.0

    def __call__(self, level: float) -> float:
        return self.kp * (self.setpoint - level)


class FakeModbusClient:
    """Deterministic Modbus client: registers return a fixed ramp."""

    def __init__(self, values=None) -> None:
        self.values = values if values is not None else {0x0000: 750, 0x0001: 120}
        self.writes: list = []

    def connect(self):
        return True

    def read_holding_registers(self, address, count=1, unit=1):
        outer = self

        class _R:
            registers = [outer.values.get(address, 0) for _ in range(count)]

            def isError(self):
                return False

        return _R()

    def write_register(self, address, value, unit=1):
        # Must mirror pymodbus's response shape: ModbusIO.write_valve calls
        # wr.isError() on the result, so returning a bare int raises
        # AttributeError before the probe ever reaches its assertion.
        self.writes.append((address, value))

        class _W:
            def isError(self):
                return False

        return _W()


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeSession:
    """Deterministic HTTP session for the SCADA client."""

    def __init__(self, demand=120.0, runtimes=(10, 5, 20)) -> None:
        self.demand = demand
        self.runtimes = list(runtimes)
        self.posts: list = []

    def get(self, url, timeout=None):
        if url.endswith("/demand"):
            return FakeResponse({"demand": self.demand})
        return FakeResponse({"runtimes": self.runtimes})

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return FakeResponse({"ok": True}, status=202)


class FakeMqttClient:
    """Records published messages instead of sending them."""

    def __init__(self) -> None:
        self.published: list = []

    def connect(self, *a, **k):
        return 0

    def publish(self, topic, payload=None, **k):
        self.published.append((topic, payload))
        outer = self

        class _Info:
            rc = 0
            mid = len(outer.published)

        return _Info()

    def loop_start(self):
        return None

    def disconnect(self):
        return None


class RecordingNotifier:
    """Captures notifications so alert behaviour is observable."""

    def __init__(self) -> None:
        self.sent: list = []

    def notify(self, subject, body):
        self.sent.append((subject, body))
        return "recorded"


# ── B: fakes for the injected-client subjects ─────────────────────────────────
#
# B1-B3 all share one shape: a `_default_*` factory that constructs the real
# library's client, and a class that takes that client by injection. Every
# corpus test injects a fake, so the factory -- the ONLY code the migration
# touches -- is never executed by anything. That is the same blind spot C1 and
# C2 had, and unlike category A it is the dominant one here.
#
# So the probes come in two halves. These fakes pin the normalisation,
# encoding and error handling around the client, deterministically and with no
# network. The live-server probe below then exercises the factory for real.


class FakeHttpResponse:
    """Minimal stand-in for requests/httpx response objects."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self._payload


class RecordingHttpSession:
    """Serves a fixed table and records how it was called."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append([url, timeout])
        return self.table[url.rsplit("/", 1)[-1]]


class FakeModbusResult:
    """pymodbus-shaped result: `.registers` plus `.isError()`."""

    def __init__(self, registers, error=False):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


class RecordingModbusClient:
    """Returns words per address and records the unit each call used."""

    def __init__(self, table, failing=()):
        self.table = table
        self.failing = set(failing)
        self.calls = []

    def read_holding_registers(self, address, count, unit=None):
        self.calls.append([address, count, unit])
        if address in self.failing:
            return FakeModbusResult([], error=True)
        return FakeModbusResult(self.table[address])


class FakePublishInfo:
    def __init__(self, rc=0):
        self.rc = rc


class RecordingMqttClient:
    """paho-shaped publish returning an object with `.rc`."""

    def __init__(self, rc=0):
        self.rc = rc
        self.calls = []

    def publish(self, topic, payload, qos=0):
        self.calls.append([topic, payload.decode("utf-8")
                           if isinstance(payload, bytes) else payload, qos])
        return FakePublishInfo(self.rc)


def _serve_fixed_json(table):
    """Start a real local HTTP server returning *table* as JSON. Returns
    ``(base_url, shutdown)``.

    A real server, not a fake, because the point is to execute
    ``_default_session()`` -- the one function the B1 migration rewrites and the
    one the corpus test never calls. `requests` and `httpx` differ in session
    construction, timeout semantics and the exception type raised by
    ``raise_for_status``, and none of that is observable through an injected
    fake.

    Bound to 127.0.0.1 on an ephemeral port: no external traffic, and nothing in
    the recorded output mentions the port, which would otherwise change on every
    run and make the golden vector unreproducible.
    """
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                                  # noqa: N802
            tag = self.path.rsplit("/", 1)[-1]
            if tag not in table:
                self.send_response(404)
                self.end_headers()
                return
            body = _json.dumps(table[tag]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):                      # noqa: D102
            pass                                  # keep the probe output clean

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]

    def shutdown():
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return f"http://{host}:{port}", shutdown


# ── probes ────────────────────────────────────────────────────────────────────
# Each entry maps a probe name to a callable taking the imported module
# namespace and returning a JSON-able observation.

def _b1(m) -> dict:
    poller_mod = m["scada_poller"]
    out: dict = {}

    # ── the injected half: normalisation around the client ────────────────────
    table = {
        "PT101": FakeHttpResponse({"value": 12.0, "quality": "GOOD"}),
        "TT200": FakeHttpResponse({"value": 300.5}),          # quality defaults
        "FT300": FakeHttpResponse({"value": 0, "quality": "BAD"}),
        "ZT400": FakeHttpResponse({"value": None, "quality": "UNCERTAIN"}),
        "ST500": FakeHttpResponse({"value": "open", "extra": "ignored"}),
    }
    session = RecordingHttpSession(table)
    poller = poller_mod.ScadaPoller("http://scada/", session=session, timeout=1.5)
    out["records"] = poller.poll(sorted(table))
    # The URL and timeout the client is actually handed. `httpx` and `requests`
    # both accept `timeout=`, but a glue layer that drops it would be invisible
    # in the returned records.
    out["calls"] = session.calls

    # Trailing slashes on base_url must not double up in the path.
    for base in ("http://scada", "http://scada/", "http://scada///",
                 "http://scada/api"):
        s2 = RecordingHttpSession({"PT101": FakeHttpResponse({"value": 1})})
        poller_mod.ScadaPoller(base, session=s2).read_tag("PT101")
        out.setdefault("base_urls", []).append([base, s2.calls[0][0]])

    # The default timeout the program applies when the caller gives none. A
    # clone that drops it hangs forever on a dead gateway instead of failing.
    s3 = RecordingHttpSession({"PT101": FakeHttpResponse({"value": 1})})
    poller_mod.ScadaPoller("http://scada", session=s3).read_tag("PT101")
    out["default_timeout"] = s3.calls[0][1]

    out["empty_poll"] = poller_mod.ScadaPoller(
        "http://scada", session=RecordingHttpSession({})).poll([])

    # Error paths, recorded as "did it raise" rather than by exception class.
    # `requests.raise_for_status` raises `requests.exceptions.HTTPError` and
    # `httpx` raises `httpx.HTTPStatusError`; a clone cannot raise the outgoing
    # library's class without importing the library it was told to replace, so
    # demanding the type would make the subject unmigratable -- the same error a
    # judge rubric made by scoring a correct reimplementation MAJOR for exactly
    # this. What matters is that a failed request does not return a record.
    for status in (400, 404, 500, 503):
        s4 = RecordingHttpSession({"BAD": FakeHttpResponse({}, status=status)})
        p4 = poller_mod.ScadaPoller("http://scada", session=s4)
        try:
            p4.read_tag("BAD")
            out.setdefault("http_errors", []).append([status, "NO RAISE"])
        except Exception:                                     # noqa: BLE001
            out.setdefault("http_errors", []).append([status, "raised"])

    # A payload with no "value" key is a malformed gateway response; the program
    # indexes it directly, so it raises. Recorded so a clone that invents a
    # default instead is caught.
    s5 = RecordingHttpSession({"PT101": FakeHttpResponse({"quality": "GOOD"})})
    try:
        poller_mod.ScadaPoller("http://scada", session=s5).read_tag("PT101")
        out["missing_value"] = "NO RAISE"
    except Exception as exc:                                  # noqa: BLE001
        out["missing_value"] = type(exc).__name__

    # ── the live half: _default_session() against a real server ───────────────
    #
    # This is what the corpus cannot see. Constructing ScadaPoller WITHOUT a
    # session runs the factory, which imports and uses the HTTP library.
    base_url, shutdown = _serve_fixed_json({
        "PT101": {"value": 12.0, "quality": "GOOD"},
        "TT200": {"value": 300.5},
    })
    try:
        live = poller_mod.ScadaPoller(base_url, timeout=5.0)
        out["live_records"] = live.poll(["PT101", "TT200"])
        try:
            live.read_tag("NOSUCHTAG")                        # the server 404s
            out["live_404"] = "NO RAISE"
        except Exception:                                     # noqa: BLE001
            out["live_404"] = "raised"
    finally:
        shutdown()
    return out


def _b2(m) -> dict:
    reader_mod, registers = m["modbus_meter_reader"], m["registers"]
    out: dict = {}

    # ── pure decoding ────────────────────────────────────────────────────────
    out["register_map"] = {name: list(spec)
                           for name, spec in sorted(registers.REGISTERS.items())}
    words = [[0x0000, 0x0000], [0x3F80, 0x0000], [0x4248, 0x0000],
             [0xC148, 0x0000], [0xFFFF, 0xFFFF], [0x0001, 0x0000],
             [0x7F7F, 0xFFFF]]
    out["decode_f32"] = [[w, registers.decode("f32", w, 1.0)] for w in words]
    out["decode_u32"] = [[w, registers.decode("u32", w, 0.001)] for w in words]
    out["decode_scaled"] = [registers.decode("u32", [0x0000, 0x2710], s)
                            for s in (1.0, 0.001, 10.0, -1.0)]
    # Words are masked to 16 bits before packing, so an out-of-range word is
    # truncated rather than raising.
    out["decode_masked"] = [registers.decode("u32", [0x1FFFF, 0x0000], 1.0),
                            registers.decode("u32", [-1, 0x0000], 1.0)]
    try:
        registers.decode("nope", [0, 0], 1.0)
        out["decode_unknown"] = "NO RAISE"
    except Exception as exc:                                  # noqa: BLE001
        out["decode_unknown"] = type(exc).__name__

    # ── the injected half ────────────────────────────────────────────────────
    table = {0x0000: [0x4248, 0x0000],      # 50.0 V
             0x0002: [0x3F80, 0x0000],      # 1.0 A
             0x0004: [0x4248, 0x0000],      # 50.0 W
             0x0006: [0x0000, 0x2710]}      # 10000 Wh -> 10.0 kWh
    client = RecordingModbusClient(table)
    reader = reader_mod.MeterReader(client=client, unit=7)
    out["measurement"] = reader.read_measurement()
    # Address, count and unit per call: a glue layer that loses `unit` talks to
    # the wrong slave, and nothing in the decoded record would show it.
    out["calls"] = client.calls

    # isError() must become an IOError, not a silently-dropped field.
    failing = RecordingModbusClient(table, failing=[0x0004])
    try:
        reader_mod.MeterReader(client=failing).read_measurement()
        out["read_error"] = "NO RAISE"
    except Exception as exc:                                  # noqa: BLE001
        out["read_error"] = [type(exc).__name__, str(exc)]
    return out


def _b3(m) -> dict:
    bridge_mod, config_mod, telemetry = (m["bridge"], m["mqtt_config"],
                                         m["telemetry"])
    out: dict = {}

    # ── pure topic and payload construction ──────────────────────────────────
    out["topics"] = [telemetry.topic_for(base, tag)
                     for base, tag in (("plant/telemetry", "PT101"),
                                       ("plant/telemetry/", "PT101"),
                                       ("", "PT101"),
                                       ("a/b/c", "d/e"))]
    readings = [{"value": 12.0, "ts": "2026-01-01T00:00:00Z", "quality": "GOOD"},
                {"value": 0, "ts": "t"},                      # quality defaults
                {"value": None, "ts": "t", "quality": "BAD"},
                {"value": "open", "ts": "t", "quality": "UNCERTAIN"},
                {"value": 1 / 3, "ts": "t"},
                {"value": True, "ts": "t"},
                {"value": [1, 2], "ts": "t", "extra": "dropped"}]
    out["payloads"] = [telemetry.encode(r).decode("utf-8") for r in readings]

    # ── config defaults and unknown-key filtering ────────────────────────────
    out["config_default"] = [config_mod.BridgeConfig().host,
                             config_mod.BridgeConfig().port,
                             config_mod.BridgeConfig().base_topic,
                             config_mod.BridgeConfig().qos,
                             config_mod.BridgeConfig().client_id]
    built = config_mod.BridgeConfig.from_dict(
        {"host": "broker", "port": 8883, "qos": 2, "nonsense": 1})
    out["config_from_dict"] = [built.host, built.port, built.qos,
                               built.base_topic, built.client_id]

    # ── the injected half ────────────────────────────────────────────────────
    cfg = config_mod.BridgeConfig(host="broker", port=1883,
                                  base_topic="plant/telemetry", qos=1,
                                  client_id="bridge-01")
    client = RecordingMqttClient()
    bridge = bridge_mod.TelemetryBridge(cfg, client=client)
    out["publish"] = bridge.publish_reading("PT101", readings[0])
    out["batch"] = bridge.publish_batch({"PT101": readings[0],
                                         "TT200": readings[1]})
    # Topic, payload and QoS as the client actually received them. A glue layer
    # that defaults qos to 0 changes delivery guarantees with no visible effect
    # on the returned record, because the record reports cfg.qos rather than
    # whatever was passed.
    out["client_calls"] = client.calls
    out["batch_empty"] = bridge.publish_batch({})

    # A non-zero broker return code must be surfaced, not swallowed.
    failing = RecordingMqttClient(rc=4)
    out["publish_rc"] = bridge_mod.TelemetryBridge(
        cfg, client=failing).publish_reading("PT101", readings[0])
    return out


def _c1(m) -> dict:
    ctrl, io_map, plant = m["controller"], m["io_map"], m["plant"]
    out: dict = {}

    # ADC/DAC scaling across the full span, including the clamp.
    out["scaling"] = [
        [io_map.counts_to_level(c) for c in (0, 1024, 2048, 4095)],
        [io_map.speed_to_counts(s) for s in (-10.0, 0.0, 50.0, 100.0, 150.0)],
    ]

    # Setpoint ramping: below target, at target, and overshoot-free arrival.
    c = ctrl.PumpController(setpoint=5.0, ramp=0.5, pid=FakePID(1.0))
    out["ramp"] = [[round(c.update(0.0), 6), round(c.applied_setpoint, 6)]
                   for _ in range(12)]

    # Closed loop against the plant model — carries state across steps.
    tank = plant.Tank(level=0.0)
    c2 = ctrl.PumpController(setpoint=6.0, ramp=1.0, pid=FakePID(2.0))
    trace = []
    level = 0.0
    for _ in range(20):
        speed = c2.update(level)
        level = tank.step(speed)
        trace.append([round(speed, 6), round(level, 6)])
    out["closed_loop"] = trace

    # ── the substituted engine itself ─────────────────────────────────────
    # Everything above injects FakePID, so none of it touches the library the
    # migration replaces. The suite does the same: all five C1 tests pass a
    # FakePID. Layer 5 does not reach it either -- it looks for factories named
    # `_default_*`, not `_make_pid`. So before this section NOTHING in the
    # corpus specified the PID engine's behaviour, and a first-party
    # replacement could compute anything at all and still pass every check.
    #
    # A FRESH engine per input, deliberately. simple-pid defaults
    # `sample_time=0.01` and returns its previous output for any call inside
    # that window, so a loop over inputs in a tight probe returns the first
    # value four times over. Constructing per input takes the first-call path
    # every time, which is the response actually under test.
    #
    # Ki=0 and Kd=0, also deliberately. With a non-zero integral term the
    # output depends on wall-clock dt and is not reproducible: three identical
    # constructions here differ in the ninth decimal, which a zero-tolerance
    # oracle would flag as a divergence on every run. The proportional
    # response is exactly determined, and it pins the properties that actually
    # matter across implementations -- sign convention, gain, and both output
    # limits.
    engine = []
    for level in (-10.0, 0.0, 1.0, 2.5, 5.0, 6.0, 100.0):
        pid = ctrl._make_pid(2.0, 0.0, 0.0, 5.0)
        engine.append([level, round(float(pid(level)), 9)])
    out["engine_proportional"] = engine

    # The setpoint is assignable and takes effect on the next call: the
    # controller relies on this every step through _ramp_setpoint.
    # Fresh per assignment, for the same reason as above: reusing one engine
    # here returned the first output three times over, which recorded
    # "the setpoint has no effect" as the expected behaviour.
    setpoint_effect = []
    for sp in (0.0, 2.0, 8.0, 20.0):
        pid = ctrl._make_pid(1.0, 0.0, 0.0, 0.0)
        pid.setpoint = sp
        setpoint_effect.append([sp, round(float(pid(1.0)), 9)])
    out["engine_setpoint"] = setpoint_effect
    return out


def _c2(m) -> dict:
    alarms, fsm, process = m["alarms"], m["fsm"], m["process"]
    out: dict = {}

    # Every alarm band, including both edges.
    out["alarms"] = [sorted(alarms.evaluate(v))
                     for v in (-1.0, 0.0, 0.5, 1.0, 4.0, 8.0, 8.5, 9.5, 11.0)]
    out["interlock"] = [alarms.interlock_ok(v)
                        for v in (-1.0, 0.5, 5.0, 9.0, 11.0)]

    # State machine driven through fill -> high -> drain -> low.
    #
    # The previous version of this probe never called `c.step(level)` and never
    # wired the controller's valve commands into the process, so `Process.apply`
    # saw both valves shut and returned 0.0 forever while the machine sat in
    # IDLE. The recorded golden vector was forty identical rows of
    # `[0.0, "IDLE", True]`: it asserted that a state machine does not move.
    # Any reimplementation that merely constructs without raising satisfied it.
    #
    # Driving it properly exercises the `transitions` engine the migration has
    # to replace: guarded entry, an automatic transition on the high limit,
    # a multi-source transition, and the wildcard trip.
    c = fsm.TankController(high=8.0, low=1.0)
    proc = process.Process()
    trace = []
    c.start_fill()                       # IDLE -> FILLING, guarded by can_fill
    for step in range(60):
        # The controller owns the valves; the process integrates them.
        proc.inlet, proc.outlet = bool(c.inlet), bool(c.outlet)
        level = proc.apply(fill_rate=0.5, drain_rate=0.4)
        c.step(level)                    # automatic transitions
        if step == 30 and str(getattr(c, "state", "")) == "HOLDING":
            c.start_drain()              # HOLDING -> DRAINING
        trace.append([round(level, 6), str(getattr(c, "state", None)),
                      bool(c.inlet), bool(c.outlet)])
    out["fsm_trace"] = trace

    # The wildcard trip fires from any state and latches: an interlock breach
    # must reach FAULT and stay there.
    t = fsm.TankController(high=8.0, low=1.0)
    t.start_fill()
    t.step(99.0)                         # above HIHI -> trip
    out["fsm_trip"] = [str(getattr(t, "state", None)),
                       bool(t.inlet), bool(t.outlet)]
    t.step(2.0)                          # still faulted, valves still shut
    out["fsm_trip_latches"] = [str(getattr(t, "state", None)),
                               bool(t.inlet), bool(t.outlet)]

    # Latching has to be checked with an EVENT, not another step(). After a
    # trip, step() takes no branch -- the state is neither FILLING nor
    # DRAINING -- so it fires nothing and any machine appears to latch. A
    # variant that silently resets FAULT to IDLE on the next event passed the
    # check above while being plainly wrong.
    # The exception is caught, not recorded. `transitions` raises MachineError
    # for a trigger that has no transition from the current state; a
    # hand-rolled machine will not raise that class, and demanding it would
    # make reimplementation impossible by definition. What must hold in any
    # implementation is the OUTCOME: the machine does not resume filling and
    # the inlet stays shut.
    try:
        t.start_fill()
    except Exception:                    # noqa: BLE001 - outcome is the contract
        pass
    out["fsm_trip_latches_on_event"] = [str(getattr(t, "state", None)),
                                        bool(t.inlet), bool(t.outlet)]

    # The guard has to be checked where it BITES. The trace above starts
    # filling from an empty tank, where can_fill() is true anyway, so dropping
    # the guard entirely changed nothing observable. Above the interlock trip
    # point the transition must be refused and the inlet must stay shut.
    g = fsm.TankController(high=8.0, low=1.0)
    g._level = 99.0
    try:
        g.start_fill()
    except Exception:                    # noqa: BLE001 - outcome is the contract
        pass
    out["fsm_guard_blocks_fill"] = [str(getattr(g, "state", None)),
                                    bool(g.inlet), bool(g.can_fill())]
    return out


def _c3(m) -> dict:
    engine, cfg_mod, hyst = m["engine"], m["engine_config"], m["hysteresis"]
    out: dict = {}

    # Hysteresis must latch: rising past high, then falling between the bands.
    h = hyst.Hysteresis(high=10.0, low=4.0)
    out["hysteresis"] = [h.update(v) for v in
                         (0.0, 5.0, 9.9, 10.0, 12.0, 8.0, 4.1, 4.0, 1.0, 11.0)]

    cfg = cfg_mod.load({"tags": [
        {"tag": "T1", "high": 10.0, "low": 4.0},
        {"tag": "T2", "high": 50.0, "low": 20.0},
    ]})
    note = RecordingNotifier()
    eng = engine.AnomalyEngine(config=cfg, notifier=note)
    readings = [
        {"tag": "T1", "value": 1.0}, {"tag": "T1", "value": 11.0},
        {"tag": "T1", "value": 5.0}, {"tag": "T1", "value": 3.0},
        {"tag": "T2", "value": 60.0}, {"tag": "T2", "value": 10.0},
    ]
    out["stream"] = [str(r) for r in eng.process_stream(readings)]
    out["notifications"] = [list(s) for s in note.sent]
    return out


def _d1(m) -> dict:
    control, transport, supervisor = m["control"], m["transport"], m["supervisor"]
    out: dict = {}

    # Control law over a temperature sweep, with integral state carried.
    ctl = control.BoilerControl(setpoint_c=80.0)
    out["control"] = [round(ctl.step(t, 100.0), 6)
                      for t in (20.0, 40.0, 60.0, 79.0, 80.0, 85.0, 95.0)]
    ctl.reset()
    out["after_reset"] = round(ctl.step(20.0, 100.0), 6)

    client = FakeModbusClient()
    io = transport.ModbusIO(client=client)
    out["io"] = [round(io.read_temperature(), 6), round(io.read_pressure(), 6)]

    sup = supervisor.BoilerSupervisor(io=io)
    out["cycles"] = [{k: (round(v, 6) if isinstance(v, float) else v)
                      for k, v in sorted(sup.cycle().items())}
                     for _ in range(6)]
    out["writes"] = [list(w) for w in client.writes]
    return out


def _d2(m) -> dict:
    client_mod, sched_mod, station_mod = m["client"], m["scheduler"], m["station"]
    out: dict = {}

    s = sched_mod.PumpScheduler()
    out["required"] = [s.required_count(d)
                       for d in (0.0, 1.0, 49.0, 50.0, 51.0, 120.0, 500.0)]
    out["select"] = [str(s.select(d, [10, 5, 20]))
                     for d in (0.0, 60.0, 120.0, 200.0)]

    sess = FakeSession()
    cl = client_mod.ScadaClient("http://x", session=sess)
    out["client"] = [cl.get_demand(), cl.get_runtimes()]

    st = station_mod.PumpStation(client=cl)
    out["cycles"] = [str(st.cycle()) for _ in range(3)]
    out["posts"] = [[u, j] for u, j in sess.posts]
    return out


def _d3(m) -> dict:
    est_mod, gw_mod, sink_mod = m["estimator"], m["gateway"], m["sink"]
    out: dict = {}

    # Estimator: fill the window, then a rate-of-change spike.
    e = est_mod.Estimator(scale=0.1, window=5, roc_limit=5.0)
    seq = [0, 100, 200, 300, 400, 500, 5000, 500, 0]
    out["estimator"] = [{k: (round(v, 6) if isinstance(v, float) else v)
                         for k, v in sorted(e.update(r).items())} for r in seq]

    client = FakeMqttClient()
    sink = sink_mod.MqttSink(client=client)
    counts = {"A": 100, "B": 250}
    gw = gw_mod.TelemetryGateway(reader=lambda t: counts[t], sink=sink)
    out["gateway"] = [{k: (round(v, 6) if isinstance(v, float) else v)
                       for k, v in sorted(rec.items())}
                      for rec in gw.process_all(["A", "B", "A", "B"])]
    out["published"] = [[t, str(p)] for t, p in client.published]
    return out


# ── A: pure-computation subjects ──────────────────────────────────────────────
#
# A1-A3 had NO probes at all: golden vectors existed only for C and D, so
# `oracle_equiv` was blank for a third of the corpus and `gen_success` (compiles
# + tests pass) was the only signal.
#
# The blind spot here is NOT the one C1 and C2 have. Those inject fakes into
# every test, so the migrated path is never executed. The A tests call the real
# functions, which call the real library -- so the path IS exercised. What is
# missing is input COVERAGE: 5, 3 and 4 test cases respectively, all on
# well-formed input, against three "drop-in" replacements that are not quite
# drop-in. Measured against the installed packages:
#
#   json -> ujson      `"a/b"` serialises as `"a\/b"`. The module docstring calls
#                      byte-reproducibility a hard requirement, and a SCADA tag
#                      like `Plant/Unit1/PT101` contains slashes.
#   datetime -> ciso8601
#                      lowercase `z` is accepted where the original raises
#                      ValueError; `T24:00:00` rolls over to the next day where
#                      the original raises.
#   zlib -> fastcrc    only `crc32.iso_hdlc` reproduces `zlib.crc32`; `bzip2`
#                      agrees on empty and all-ones input and diverges elsewhere.
#                      The existing test does pin one known value, so this one is
#                      already partly covered.
#
# Everything here is pure computation with no clock, no network and no
# randomness, so these are exact golden vectors rather than the
# tolerance-based invariants C1 needed.


def _a1(m) -> dict:
    crc = m["crc_telemetry"]
    out: dict = {}

    # A spread of payloads: empty, single byte, the reference vector, every byte
    # value, a long run, and a realistic frame.
    payloads = [b"", b"\x00", b"\xff", b"123456789", bytes(range(256)),
                b"a" * 1000, b"tag=PT101;val=42", b"Plant/Unit1/PT101",
                b"\xff\xff\xff\xff", b"\x01\x03\x02\x00\x64"]
    out["crc32"] = [[payload.hex(), crc.crc32(payload)] for payload in payloads]

    # The trailer is big-endian and exactly CRC_LEN bytes; the frame is the
    # payload plus that trailer and nothing else.
    out["frames"] = [[payload.hex(), crc.build_frame(payload).hex()]
                     for payload in payloads]
    out["verify_roundtrip"] = [crc.verify_frame(crc.build_frame(p))
                               for p in payloads]
    out["extract_roundtrip"] = [crc.extract_payload(crc.build_frame(p)).hex()
                                for p in payloads]

    # Frames shorter than the trailer, and a frame that is EXACTLY the trailer
    # (an empty payload). The corpus tests a 1-byte frame but not the 4-byte
    # boundary, where `frame[:-4]` is empty rather than wrong.
    short = [b"", b"\x00", b"\x00\x01", b"\x00\x01\x02", b"\x00\x01\x02\x03"]
    out["verify_short"] = [[f.hex(), crc.verify_frame(f)] for f in short]

    # Corruption in every byte position of a payload must be detected.
    detected = []
    base = b"tag=PT101;val=42"
    for index in range(len(base)):
        frame = bytearray(crc.build_frame(base))
        frame[index] ^= 0xFF
        detected.append(crc.verify_frame(bytes(frame)))
    out["corruption_detected"] = detected

    # And the error PATH: extract_payload raises on a bad CRC. The type matters,
    # because callers catch it.
    bad = bytearray(crc.build_frame(b"abc"))
    bad[0] ^= 0xFF
    try:
        crc.extract_payload(bytes(bad))
        out["extract_bad_crc"] = "NO EXCEPTION"
    except Exception as exc:                                  # noqa: BLE001
        out["extract_bad_crc"] = type(exc).__name__
    return out


def _a2(m) -> dict:
    logger = m["sensor_json_logger"]
    out: dict = {}

    # Value types the serialiser can meet. `1` vs `1.0` vs `True` are distinct
    # in JSON and a backend that conflates them changes the audit record.
    # NaN and the infinities are in here because this is a SENSOR logger: a
    # failed transmitter reports NaN routinely, and `json` writes it as `NaN`
    # while `orjson` silently writes `null`. That collapses "the sensor returned
    # not-a-number" into "there was no reading", which are different facts in an
    # audit log and are acted on differently downstream.
    #
    # The large integer is here for the same reason at the other extreme:
    # `orjson` raises TypeError above 64 bits where `json` serialises fine, so a
    # counter or bitmask value crashes the logger outright.
    values = [42.5, 1, 1.0, 0, -0.0, True, False, None, "text", "",
              0.1 + 0.2, 1e-20, 2 ** 70, [1, 2, 3], {"k": "v"}, 1 / 3,
              float("nan"), float("inf"), float("-inf")]
    out["values"] = [logger.format_reading("PT101", v, "2026-01-01T00:00:00+00:00")
                     for v in values]

    # Tags that exercise escaping. The slash is the one that matters: `ujson`
    # writes `a\/b` where `json` writes `a/b`, and this module's docstring makes
    # byte-reproducibility a hard requirement.
    tags = ["PT101", "Plant/Unit1/PT101", "Temp °C", "a\"b", "a\\b",
            "tab\there", "nl\nhere", "", "pump \U0001f6e0", "été"]
    out["tags"] = [logger.format_reading(t, 1, "t") for t in tags]

    # Quality is a plain string but it is part of the canonical record.
    out["quality"] = [logger.format_reading("T", 1, "t", quality=q)
                      for q in ("GOOD", "BAD", "UNCERTAIN", "", "a/b")]

    # Batch framing: ordering, the default quality, and the empty batch.
    out["batch"] = logger.format_batch([
        {"tag": "T1", "value": 1, "ts": "t1"},
        {"tag": "T2", "value": 2, "ts": "t2", "quality": "UNCERTAIN"},
        {"tag": "T3/sub", "value": None, "ts": "t3"},
    ])
    out["batch_empty"] = logger.format_batch([])
    out["batch_single"] = logger.format_batch([{"tag": "X", "value": 0, "ts": "t"}])
    return out


def _a3(m) -> dict:
    parser = m["iso_ts_parser"]
    out: dict = {}

    # Inputs the ORIGINAL accepts. Recorded as isoformat plus epoch plus offset
    # rather than repr: `ciso8601` returns its own tzinfo class, so repr differs
    # for a parse that is in every observable way identical, and a probe keyed on
    # repr would reject a correct migration.
    accepted = [
        "2026-01-01T00:00:00", "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+01:00",
        "2026-01-01T00:00:00-05:30", "2026-01-01T00:00:00.500+00:00",
        "2026-01-01T00:00:00.000001Z", "2026-01-01", "2026-01-01T00:00",
        "20260101T000000", "2026-01-01 00:00:00",
        "2026-01-01T00:00:00-00:00", "1970-01-01T00:00:00Z",
        "2000-02-29T12:34:56Z", "2038-01-19T03:14:07Z",
    ]
    rows = []
    for text in accepted:
        dt = parser.parse(text)
        offset = dt.utcoffset()
        rows.append([
            text,
            dt.isoformat(),
            round(parser.to_epoch(text), 6),
            None if offset is None else offset.total_seconds(),
            dt.tzinfo is None,
        ])
    out["accepted"] = rows

    # Inputs that are MALFORMED under any reading, recorded as the exception
    # type. A parser that accepts "2026-13-01" is broken, and the type matters
    # because callers catch it.
    #
    # Deliberately NOT included: inputs the stdlib rejects merely because it is
    # stricter than ISO 8601. `ciso8601` accepts a lowercase `z` and maps
    # `T24:00:00` to the following midnight; the standard permits both, and the
    # corpus names Z-tolerance as the very motivation for the swap. Gating on
    # those would make A3 unmigratable for something that is not a defect --
    # the same error as a judge rubric that scored a correct reimplementation
    # MAJOR because it could not raise the removed library's exception class.
    # Verified: on all twelve inputs below, `datetime.fromisoformat` and
    # `ciso8601` agree, so this key discriminates a broken parser without
    # penalising a more tolerant one.
    rejected = ["not-a-date", "", "2026-13-01T00:00:00", "2026-01-32T00:00:00",
                "2026-01-01T25:00:00", "2026-01-01T00:60:00",
                "2026-01-01T00:00:00+25:00", "01-01-2026", "2026/01/01",
                "2026-02-30T00:00:00", "abc123", "2026-01-01T"]
    verdicts = []
    for text in rejected:
        try:
            verdicts.append([text, "PARSED: " + parser.parse(text).isoformat()])
        except Exception as exc:                               # noqa: BLE001
            verdicts.append([text, type(exc).__name__])
    out["rejected"] = verdicts
    return out


PROBES = {
    "A1_crc_telemetry": _a1,
    "A2_sensor_json_logger": _a2,
    "A3_iso_ts_parser": _a3,
    "B1_scada_poller": _b1,
    "B2_modbus_meter_reader": _b2,
    "B3_mqtt_telemetry_bridge": _b3,
    "C1_pid_pump_controller": _c1,
    "C2_tank_level_fsm": _c2,
    "C3_anomaly_threshold_engine": _c3,
    "D1_boiler_control_loop": _d1,
    "D2_pump_station_scheduler": _d2,
    "D3_telemetry_gateway": _d3,
}


# ── invariants ────────────────────────────────────────────────────────────────
# Properties that must hold for ANY input, not just the recorded ones. These
# catch a rewrite that happens to reproduce the golden values while breaking
# behaviour the probes never visited.

def _inv_b1(m) -> list:
    """Properties the poller guarantees for any tag list and any payload."""
    poller_mod = m["scada_poller"]
    bad = []

    tags = ["PT101", "TT200", "FT300", "a-b_c", "123"]
    table = {t: FakeHttpResponse({"value": i}) for i, t in enumerate(tags)}
    session = RecordingHttpSession(table)
    records = poller_mod.ScadaPoller("http://scada", session=session).poll(tags)

    if len(records) != len(tags):
        bad.append(f"poll({len(tags)} tags) returned {len(records)} records")
    for tag, record in zip(tags, records):
        if set(record) != {"tag", "value", "quality"}:
            bad.append(f"record for {tag} has keys {sorted(record)}, expected "
                       f"tag/value/quality exactly")
            break
        if record["tag"] != tag:
            bad.append(f"record for {tag} reports tag={record['tag']!r}; "
                       f"records must stay aligned with the requested order")
            break

    # The documented default. A missing `quality` becomes GOOD, and a present
    # one is never overwritten.
    s2 = RecordingHttpSession({"X": FakeHttpResponse({"value": 1})})
    if poller_mod.ScadaPoller("http://scada", session=s2
                              ).read_tag("X")["quality"] != "GOOD":
        bad.append("a payload with no quality field must default to GOOD")
    for quality in ("BAD", "UNCERTAIN", "", "GOOD"):
        s3 = RecordingHttpSession({"X": FakeHttpResponse({"value": 1,
                                                          "quality": quality})})
        got = poller_mod.ScadaPoller("http://scada", session=s3
                                     ).read_tag("X")["quality"]
        if got != quality:
            bad.append(f"quality {quality!r} from the gateway became {got!r}")
            break

    # Every request carries a timeout. Without one a dead gateway hangs the
    # poller forever, which is the failure this parameter exists to prevent.
    s4 = RecordingHttpSession({"X": FakeHttpResponse({"value": 1})})
    poller_mod.ScadaPoller("http://scada", session=s4, timeout=3.25).read_tag("X")
    if not s4.calls or s4.calls[0][1] != 3.25:
        bad.append(f"the configured timeout did not reach the client: "
                   f"{s4.calls!r}")

    # A 4xx/5xx must not yield a record, whatever exception class carries it.
    for status in (400, 404, 500):
        s5 = RecordingHttpSession({"X": FakeHttpResponse({"value": 1},
                                                          status=status)})
        try:
            poller_mod.ScadaPoller("http://scada", session=s5).read_tag("X")
            bad.append(f"HTTP {status} returned a record instead of raising")
            break
        except Exception:                                     # noqa: BLE001
            pass
    return bad


def _inv_b2(m) -> list:
    """Decoding properties that hold for any register contents."""
    import struct as _struct

    reader_mod, registers = m["modbus_meter_reader"], m["registers"]
    bad = []

    # f32 decoding must agree with big-endian IEEE-754 for any word pair.
    for high in (0x0000, 0x3F80, 0x4248, 0xC148, 0x7F7F):
        for low in (0x0000, 0x0001, 0xFFFF):
            expected = _struct.unpack(">f", _struct.pack(">HH", high, low))[0]
            got = registers.decode("f32", [high, low], 1.0)
            if got != expected and not (got != got and expected != expected):
                bad.append(f"decode('f32', [{high:#06x}, {low:#06x}]) = {got!r}, "
                           f"expected {expected!r}")
                break
        if bad:
            break

    # u32 is an unsigned big-endian pair, scaled.
    for high, low, scale in ((0x0000, 0x2710, 0.001), (0xFFFF, 0xFFFF, 1.0),
                             (0x0001, 0x0000, 1.0)):
        expected = _struct.unpack(">I", _struct.pack(">HH", high, low))[0] * scale
        got = registers.decode("u32", [high, low], scale)
        if abs(got - expected) > 1e-9:
            bad.append(f"decode('u32', [{high:#06x}, {low:#06x}], {scale}) = "
                       f"{got!r}, expected {expected!r}")
            break

    if not isinstance(registers.decode("u32", [0, 1], 1.0), float):
        bad.append("decode must return a float; the scale makes it one even for "
                   "integer encodings")

    try:
        registers.decode("unknown-encoding", [0, 0], 1.0)
        bad.append("decode accepted an unknown encoding instead of raising")
    except ValueError:
        pass
    except Exception as exc:                                  # noqa: BLE001
        bad.append(f"decode raised {type(exc).__name__} for an unknown "
                   f"encoding; the program raises ValueError")

    # A measurement covers every configured register, and nothing else.
    table = {address: [0x3F80, 0x0000]
             for address, _, _, _ in registers.REGISTERS.values()}
    client = RecordingModbusClient(table)
    record = reader_mod.MeterReader(client=client).read_measurement()
    if set(record) != set(registers.REGISTERS):
        bad.append(f"read_measurement returned {sorted(record)}, expected "
                   f"{sorted(registers.REGISTERS)}")
    if len(client.calls) != len(registers.REGISTERS):
        bad.append(f"read_measurement made {len(client.calls)} reads for "
                   f"{len(registers.REGISTERS)} registers")

    # A failed read must abort, not return a partial record.
    for address in list(registers.REGISTERS.values())[0][:1]:
        failing = RecordingModbusClient(table, failing=[address])
        try:
            reader_mod.MeterReader(client=failing).read_measurement()
            bad.append(f"a Modbus error at {address:#06x} produced a record "
                       f"instead of raising")
        except OSError:
            pass                     # IOError is an alias for OSError
        except Exception as exc:                              # noqa: BLE001
            bad.append(f"a Modbus error raised {type(exc).__name__}; the "
                       f"program raises IOError")
    return bad


def _inv_b3(m) -> list:
    """Payload and topic properties that hold for any reading."""
    import json as _json

    bridge_mod, config_mod, telemetry = (m["bridge"], m["mqtt_config"],
                                         m["telemetry"])
    bad = []

    readings = [{"value": 12.0, "ts": "t", "quality": "GOOD"},
                {"value": 0, "ts": "t"},
                {"value": None, "ts": "t", "quality": "BAD"},
                {"value": "open", "ts": "t", "extra": "dropped"}]

    for reading in readings:
        payload = telemetry.encode(reading)
        if not isinstance(payload, bytes):
            bad.append(f"encode returned {type(payload).__name__}, not bytes; "
                       f"MQTT payloads are bytes")
            break
        try:
            body = _json.loads(payload.decode("utf-8"))
        except Exception as exc:                              # noqa: BLE001
            bad.append(f"encode produced {payload!r}, not valid JSON: "
                       f"{type(exc).__name__}")
            break
        if set(body) != {"value", "ts", "q"}:
            bad.append(f"payload keys {sorted(body)}, expected value/ts/q "
                       f"exactly -- extra fields leak onto the wire")
            break
        if body["value"] != reading["value"] and reading["value"] is not None:
            bad.append(f"payload value {body['value']!r} != reading value "
                       f"{reading['value']!r}")
            break
        if body["q"] != reading.get("quality", "GOOD"):
            bad.append(f"payload q={body['q']!r}, expected "
                       f"{reading.get('quality', 'GOOD')!r}")
            break

    # Exactly one separator between base and tag.
    for base, tag in (("plant/telemetry", "PT101"), ("a", "b")):
        topic = telemetry.topic_for(base, tag)
        if topic != f"{base}/{tag}":
            bad.append(f"topic_for({base!r}, {tag!r}) = {topic!r}")
            break

    # The configured QoS must reach the client, not just the returned record.
    cfg_cls = config_mod.BridgeConfig
    for qos in (0, 1, 2):
        client = RecordingMqttClient()
        cfg = cfg_cls(qos=qos)
        result = bridge_mod.TelemetryBridge(cfg, client=client).publish_reading(
            "PT101", readings[0])
        if not client.calls or client.calls[0][2] != qos:
            bad.append(f"qos={qos} did not reach the client: {client.calls!r}")
            break
        if result.get("qos") != qos:
            bad.append(f"publish_reading reported qos={result.get('qos')!r} "
                       f"for a bridge configured with {qos}")
            break

    # One message per reading, in the order given.
    client = RecordingMqttClient()
    batch = {f"T{i}": {"value": i, "ts": "t"} for i in range(4)}
    results = bridge_mod.TelemetryBridge(cfg_cls(), client=client
                                         ).publish_batch(batch)
    if len(results) != len(batch) or len(client.calls) != len(batch):
        bad.append(f"publish_batch sent {len(client.calls)} messages and "
                   f"returned {len(results)} results for {len(batch)} readings")
    if [r["topic"].rsplit("/", 1)[-1] for r in results] != list(batch):
        bad.append("publish_batch did not preserve the order of its readings")

    # Unknown keys must not reach the dataclass constructor.
    try:
        built = cfg_cls.from_dict({"host": "h", "nonsense": 1})
        if built.host != "h":
            bad.append(f"from_dict lost a known field: host={built.host!r}")
    except Exception as exc:                                  # noqa: BLE001
        bad.append(f"from_dict raised {type(exc).__name__} on an unknown key; "
                   f"unknown keys are filtered, not fatal")
    return bad


def _inv_c1(m) -> list:
    """Only properties the ORIGINAL actually guarantees.

    An earlier version asserted counts_to_level stays within 0..10. It does
    not — the function is a plain linear map with no clamp, so the pristine
    program returns -1.22 for -500 counts. An invariant the original violates
    rejects a perfect copy, which would have made the whole oracle useless
    while looking rigorous. What the original does guarantee is monotonicity
    and the clamp on the DAC side, so those are what is checked.
    """
    io_map = m["io_map"]
    bad = []

    previous = None
    for counts in range(-500, 5000, 37):
        level = io_map.counts_to_level(counts)
        if previous is not None and level < previous:
            bad.append(f"counts_to_level not monotonic at {counts}")
            break
        previous = level

    # speed_to_counts DOES clamp — its docstring and the corpus test say so.
    for pct in range(-50, 200, 7):
        c = io_map.speed_to_counts(float(pct))
        if not (0 <= c <= 4095):
            bad.append(f"speed_to_counts({pct})={c} outside 0..4095")
            break

    # ── the PID engine's timing semantics ────────────────────────────────────
    #
    # These are invariants rather than golden vectors on purpose, and they exist
    # because the golden vectors above CANNOT see what they check.
    #
    # `engine_proportional` and `engine_setpoint` pin the engine with Ki=0 and
    # Kd=0 and a fresh instance per input. That was done to make them
    # reproducible: with a non-zero integral the output depends on wall-clock
    # dt and three identical constructions differ in the ninth decimal, which a
    # zero-tolerance oracle flags on every run. But removing the clock removed
    # the two behaviours that dominate how this library actually behaves in the
    # program -- which runs Ki=0.5 -- and a blind LLM review caught all three
    # first-party reimplementations diverging on exactly them:
    #
    #   simple_pid  [12.000004, 12.000004, 12.000004, ...]   (holds)
    #   rewrite 1   [12.0, 11.000009, 10.000015, ...]        (no hold)
    #   rewrite 2   [12.0, 12.0, 12.0, ...]                  (holds; first-call dt)
    #   rewrite 3   [15.0, 14.000007, 13.000012, ...]        (dt=1.0 first call)
    #
    # All three passed the golden vectors. So these two invariants are phrased
    # as tolerance-based PROPERTIES, not recorded numbers: they are exact about
    # the semantics and agnostic about the clock, which is what makes them both
    # reproducible and able to see the defect.
    controller = m["controller"]
    try:
        # 1. The sample_time hold. simple-pid defaults sample_time=0.01 and
        #    returns its previous output unchanged for any call arriving inside
        #    that window (gated on _last_output, so only from the second call
        #    on). Back-to-back calls in a tight loop are therefore IDENTICAL.
        pid = controller._make_pid(2.0, 0.5, 0.0, 6.0)
        first = float(pid(0.0))
        rapid = [float(pid(0.5 * i)) for i in range(1, 5)]
        if any(abs(v - first) > 1e-9 for v in rapid):
            bad.append(
                f"engine recomputes inside the sample_time window: "
                f"first={first!r} then {rapid!r}; simple-pid holds the "
                f"previous output until sample_time (0.01s) has elapsed"
            )

        # 2. The first-call dt. simple-pid initialises _last_time in its
        #    constructor (via reset()), so the very first call sees a dt of
        #    microseconds and the integral contributes essentially nothing --
        #    the first output is the proportional term. A rewrite that starts
        #    _last_time at None and substitutes dt=1.0 injects a whole
        #    Ki*error*1.0 step: with Ki=0.5 and error=6.0 that is +3.0 on the
        #    very first command, which shifts the entire trajectory.
        pid = controller._make_pid(2.0, 0.5, 0.0, 6.0)
        observed = float(pid(0.0))
        expected = 2.0 * 6.0                      # Kp * error, clamped to 100
        if abs(observed - expected) > 0.01:
            bad.append(
                f"first engine output {observed} is not the proportional "
                f"term {expected}: the integral contributed "
                f"{observed - expected:+.4f} on the first call, so dt was "
                f"not the near-zero elapsed time simple-pid starts from"
            )
        # 3. Derivative on MEASUREMENT, not on error. simple-pid defaults
        #    `differential_on_measurement=True`, computing
        #    `-Kd * (input - last_input) / dt`. A rewrite using
        #    `Kd * (error - last_error) / dt` is identical while the setpoint is
        #    fixed -- d_error == -d_input then -- and diverges the moment the
        #    setpoint moves, which in this program it does every single cycle
        #    through `_ramp_setpoint`.
        #
        #    This is in scope rather than library trivia: `kd` is a parameter of
        #    the program's own `PumpController.__init__`, so a caller can reach
        #    it without touching the engine directly.
        #
        #    Made clock-free by holding the INPUT constant: d_input is then 0, so
        #    the measurement derivative is exactly 0 for any dt, and the output
        #    is exactly `Kp * error`. An error-based derivative injects a
        #    nonzero, dt-dependent term instead. Observed: simple-pid gives
        #    [12.0, 10.0, 8.0] on the nose, while the two rewrites that prompted
        #    this gave [12.0, 8.07, 6.01] and [12.0, 8.01, 5.80].
        #
        #    The sleep is what makes the derivative reachable at all -- without
        #    it the sample_time hold returns the cached first output and the
        #    whole property is invisible. Three 20 ms sleeps is the entire cost.
        pid = controller._make_pid(2.0, 0.0, 0.05, 6.0)
        derivative_trace = []
        for setpoint in (6.0, 5.0, 4.0):
            pid.setpoint = setpoint
            time.sleep(0.02)                     # clear the 0.01s hold
            derivative_trace.append(round(float(pid(0.0)), 6))
        if derivative_trace != [12.0, 10.0, 8.0]:
            bad.append(
                f"engine derivative is computed on error, not on measurement: "
                f"constant input with a moving setpoint and Kd=0.05 gave "
                f"{derivative_trace} where simple-pid gives [12.0, 10.0, 8.0]; "
                f"with the input held constant the measurement derivative is "
                f"exactly zero, so the output must be Kp*error"
            )
        # 4. The zero-elapsed-time guard. simple-pid computes
        #    `dt = now - self._last_time if (now - self._last_time) else 1e-16`
        #    -- the `else 1e-16` is load-bearing. When the clock returns the
        #    same value twice (coarse platform resolution, or a constant
        #    time_fn) dt is 0, and dividing the derivative by it raises
        #    ZeroDivisionError.
        #
        #    Found by the blind judge on a rewrite that had already passed the
        #    three invariants above, then confirmed on all three clones of that
        #    round: two raised ZeroDivisionError and one saturated to 100.0
        #    where the real library returns Kp*error. Every earlier probe let
        #    the real clock advance, so none could see it.
        #
        #    Three hooks are tried together because rewrites differ in what they
        #    expose, and no single one reaches all of them:
        #      * `time.monotonic` is patched, which catches a module doing
        #        `time.monotonic()` at call time;
        #      * the instance's `time_fn` is set when present, which catches a
        #        rewrite that did `from time import monotonic as _now` and so
        #        never consults `time.monotonic` again;
        #      * private float attributes mentioning time are pinned, because
        #        the reachable path is the FIRST call seeing a zero delta -- on
        #        later calls the sample_time hold returns the cached output.
        #    `sample_time` is excluded from pinning: it is public, and
        #    overwriting it would disable the hold this file also checks.
        #
        #    Deliberately NOT a failure when no hook bites. Requiring `time_fn`
        #    to exist would import an out-of-surface demand -- the program never
        #    passes one -- and this file's standard is to check only what the
        #    program can actually reach. An unreachable property is left
        #    unverified rather than failed.
        import time as _time_mod
        _real_monotonic = _time_mod.monotonic
        try:
            _time_mod.monotonic = lambda: 1000.0
            frozen_pid = controller._make_pid(2.0, 0.5, 0.5, 6.0)
            if hasattr(frozen_pid, "time_fn"):
                frozen_pid.time_fn = lambda: 1000.0
            for name in [n for n in dir(frozen_pid)
                         if n.startswith("_") and "time" in n
                         and isinstance(getattr(frozen_pid, n, None), (int, float))]:
                setattr(frozen_pid, name, 1000.0)
            frozen_output = None
            try:
                frozen_output = float(frozen_pid(0.0))
            except ZeroDivisionError:
                bad.append(
                    "engine raises ZeroDivisionError when the clock does not "
                    "advance: simple-pid substitutes 1e-16 for a zero dt "
                    "(`dt = now - last if (now - last) else 1e-16`); without "
                    "that fallback a coarse clock crashes the controller"
                )
        finally:
            _time_mod.monotonic = _real_monotonic
        if frozen_output is not None and abs(frozen_output - 12.0) > 0.01:
            bad.append(
                f"engine returned {frozen_output} on a frozen clock where "
                f"simple-pid returns 12.0 (= Kp*error): with dt at 1e-16 the "
                f"integral and derivative contributions are negligible, so the "
                f"first output is the proportional term"
            )

    except Exception as exc:                                  # noqa: BLE001
        bad.append(f"engine timing invariants could not run: "
                   f"{type(exc).__name__}: {exc}")
    return bad


def _inv_c2(m) -> list:
    alarms = m["alarms"]
    bad = []
    for i in range(-20, 140):
        level = i / 10.0
        if not alarms.interlock_ok(level) and 1.0 <= level <= 9.0:
            bad.append(f"interlock_ok({level}) False inside safe band")
        if not isinstance(alarms.evaluate(level), (set, frozenset)):
            bad.append(f"evaluate({level}) is not a set")
    # ── invalid SOURCE raises; failed GUARD returns falsy ─────────────────────
    #
    # `transitions` distinguishes two refusals, and a faithful FSM must too:
    #
    #   * no transition leaves the current state for this event
    #         -> raises MachineError ("Can't trigger event empty from IDLE!")
    #   * a transition matches but its `conditions` guard evaluates False
    #         -> returns False, no exception
    #
    # An earlier version of this invariant checked only the first case. Two of
    # three live rewrites then over-generalised it and raised on guard failure
    # too -- a defect the invariant itself induced, found by the blind judge and
    # confirmed directly. Both halves are checked now, because "must raise" and
    # "must not raise" are each half of the behaviour.
    #
    # The guard case cannot be reached through `step()`: `_level` only changes
    # there, and any out-of-band level trips to FAULT before `start_fill` can
    # fire from IDLE. So `_level` is set directly, which is also how the
    # program's own constructor leaves it.
    #
    # The exception TYPE is deliberately not checked. A first-party FSM cannot
    # raise `transitions.MachineError` without importing the library it was told
    # to remove, so requiring it would be unsatisfiable. Callers catching the
    # library's exception do break, and that is recorded as an unavoidable
    # consequence of removing the dependency rather than as a defect.
    fsm_mod = m["fsm"]

    for event in ("empty", "level_reached", "start_drain"):
        controller = fsm_mod.TankController()
        before = controller.state
        try:
            getattr(controller, event)()
        except Exception:                                     # noqa: BLE001
            continue                                          # refused, correct
        bad.append(
            f"{event}() from state {before} was accepted silently (state is now "
            f"{controller.state}); `transitions` raises MachineError because no "
            f"{event} transition leaves {before}, so an invalid command must be "
            f"refused rather than ignored"
        )

    # The other half: a matching transition whose guard fails must NOT raise.
    controller = fsm_mod.TankController()
    controller._level = 11.0          # outside the interlock band
    if controller.state == "IDLE" and not controller.can_fill():
        try:
            result = controller.start_fill()
        except Exception as exc:                              # noqa: BLE001
            bad.append(
                f"start_fill() raised {type(exc).__name__} when its can_fill "
                f"guard failed; `transitions` returns False here and raises only "
                f"when no transition leaves the current state. IDLE does have a "
                f"start_fill transition, so this is a refused guard, not an "
                f"invalid trigger"
            )
        else:
            if controller.state != "IDLE":
                bad.append(
                    f"start_fill() moved to {controller.state} despite can_fill() "
                    f"being False; the guard must block the transition"
                )
            elif result:
                bad.append(
                    f"start_fill() returned {result!r} when its guard failed; "
                    f"`transitions` returns False"
                )

    return bad


def _inv_c3(m) -> list:
    hyst = m["hysteresis"]
    bad = []
    h = hyst.Hysteresis(high=10.0, low=4.0)
    seen = set()
    for i in range(0, 300):
        state = h.update((i % 30) / 2.0)
        seen.add(state)
    if not seen:
        bad.append("hysteresis produced no states")
    return bad


def _inv_d1(m) -> list:
    control = m["control"]
    bad = []
    ctl = control.BoilerControl(setpoint_c=80.0)
    for i in range(0, 200):
        out = ctl.step(float(i % 120), 100.0)
        if not (0.0 <= out <= 100.0):
            bad.append(f"valve command {out} outside 0..100")
            break
    return bad


def _inv_d2(m) -> list:
    sched = m["scheduler"]
    bad = []
    s = sched.PumpScheduler(n_pumps=3, pump_capacity=50.0)
    prev = -1
    for i in range(0, 400, 3):
        n = s.required_count(float(i))
        if n < 0 or n > 3:
            bad.append(f"required_count({i})={n} outside 0..3")
            break
        if n < prev:
            bad.append(f"required_count not monotonic at demand={i}")
            break
        prev = n
    return bad


def _inv_d3(m) -> list:
    est_mod = m["estimator"]
    bad = []
    e = est_mod.Estimator(scale=0.1, window=5, roc_limit=5.0)
    for i in range(0, 300, 7):
        rec = e.update(float(i))
        if not isinstance(rec, dict):
            bad.append(f"update({i}) did not return a dict")
            break
        if "value" in rec and not isinstance(rec["value"], (int, float)):
            bad.append(f"update({i}) value is not numeric")
            break
    return bad


def _inv_a1(m) -> list:
    """Properties the CRC framing guarantees for any payload."""
    crc = m["crc_telemetry"]
    bad = []

    payloads = [bytes([i]) * n for i in (0, 1, 127, 255) for n in (0, 1, 7, 64)]
    payloads += [bytes(range(256)), b"Plant/Unit1", b"\x01\x03\x02\x00\x64"]

    for payload in payloads:
        value = crc.crc32(payload)
        # Unsigned 32-bit. `zlib.crc32` is already unsigned on current Pythons,
        # but the program masks anyway, and a backend returning a signed value
        # would break `struct.pack(">I", ...)` rather than return a wrong number.
        if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
            bad.append(f"crc32({payload[:8]!r}) = {value!r}, not a uint32")
            break

    for payload in payloads:
        frame = crc.build_frame(payload)
        if len(frame) != len(payload) + crc.CRC_LEN:
            bad.append(f"build_frame({payload[:8]!r}) length {len(frame)}, "
                       f"expected {len(payload) + crc.CRC_LEN}")
            break
        if not frame.startswith(payload):
            bad.append(f"build_frame({payload[:8]!r}) does not begin with the "
                       f"payload; the trailer must be appended, not prepended")
            break
        if not crc.verify_frame(frame):
            bad.append(f"verify_frame rejected a frame it just built for "
                       f"{payload[:8]!r}")
            break
        if crc.extract_payload(frame) != payload:
            bad.append(f"extract_payload did not round-trip {payload[:8]!r}")
            break

    # Any single-bit flip inside the payload must be caught. CRC-32 guarantees
    # this, so a backend that does not is computing something else.
    base = b"tag=PT101;val=42"
    frame = crc.build_frame(base)
    for index in range(len(base)):
        for bit in (0x01, 0x80):
            corrupt = bytearray(frame)
            corrupt[index] ^= bit
            if crc.verify_frame(bytes(corrupt)):
                bad.append(f"verify_frame accepted a frame with bit {bit:#04x} "
                           f"flipped at byte {index}")
                break
        if bad:
            break

    # A frame shorter than the trailer cannot carry one.
    for length in range(crc.CRC_LEN):
        if crc.verify_frame(b"\x00" * length):
            bad.append(f"verify_frame accepted a {length}-byte frame, shorter "
                       f"than the {crc.CRC_LEN}-byte trailer")
            break
    return bad


def _inv_a2(m) -> list:
    """Canonical-form properties the audit log depends on."""
    import json as _json

    logger = m["sensor_json_logger"]
    bad = []

    samples = [("PT101", 42.5, "2026-01-01T00:00:00+00:00", "GOOD"),
               ("Plant/Unit1", 1, "t", "BAD"),
               ("Temp °C", None, "t", "UNCERTAIN"),
               ("a\"b", [1, 2], "t", "GOOD"),
               ("", 0, "", "")]

    for tag, value, ts, quality in samples:
        line = logger.format_reading(tag, value, ts, quality=quality)

        if "\n" in line:
            bad.append(f"format_reading({tag!r}) returned a multi-line record; "
                       f"format_batch joins on newlines, so an embedded one "
                       f"corrupts the batch framing")
            continue
        # Parsed with the STDLIB reader deliberately: the record has to be
        # readable by something other than whatever wrote it, which is the point
        # of an interchange format in an audit log.
        try:
            parsed = _json.loads(line)
        except Exception as exc:                               # noqa: BLE001
            bad.append(f"format_reading({tag!r}) produced {line!r}, which is "
                       f"not valid JSON: {type(exc).__name__}")
            continue
        expected = {"tag": tag, "value": value, "ts": ts, "quality": quality}
        if parsed != expected:
            bad.append(f"format_reading({tag!r}) round-tripped to {parsed!r}, "
                       f"expected {expected!r}")
            continue
        if sorted(parsed) != list(parsed):
            bad.append(f"keys are not sorted in {line!r}; the audit log must be "
                       f"byte-reproducible")
        if ", " in line or '": ' in line:
            bad.append(f"separators are not compact in {line!r}")

    # Batch framing: one line per reading, and an empty batch is an empty string
    # rather than a blank line.
    readings = [{"tag": f"T{i}", "value": i, "ts": "t"} for i in range(5)]
    batch = logger.format_batch(readings)
    if batch.count("\n") != len(readings) - 1:
        bad.append(f"format_batch produced {batch.count(chr(10)) + 1} lines for "
                   f"{len(readings)} readings")
    if logger.format_batch([]) != "":
        bad.append(f"format_batch([]) returned "
                   f"{logger.format_batch([])!r}, expected an empty string")
    return bad


def _inv_a3(m) -> list:
    """Properties the timestamp parser guarantees for any accepted input."""
    from datetime import datetime as _datetime

    parser = m["iso_ts_parser"]
    bad = []

    texts = ["2026-01-01T00:00:00", "2026-01-01T00:00:00Z",
             "2026-01-01T00:00:00+01:00", "2026-01-01T00:00:00-05:30",
             "2026-06-30T23:59:59.999999+00:00", "1970-01-01T00:00:00Z",
             "2026-01-01"]

    for text in texts:
        dt = parser.parse(text)
        if not isinstance(dt, _datetime):
            bad.append(f"parse({text!r}) returned {type(dt).__name__}, "
                       f"not a datetime")
            continue
        # The module's whole contract: naive input is assumed UTC, so the result
        # is ALWAYS aware. A backend that returns a naive datetime silently
        # changes every downstream epoch conversion.
        if dt.tzinfo is None or dt.utcoffset() is None:
            bad.append(f"parse({text!r}) returned a naive datetime; naive input "
                       f"is documented as UTC, so the result must be aware")
            continue
        if abs(parser.to_epoch(text) - dt.timestamp()) > 1e-9:
            bad.append(f"to_epoch({text!r}) = {parser.to_epoch(text)!r} but "
                       f"parse(...).timestamp() = {dt.timestamp()!r}")

    # A naive timestamp and the same instant written as UTC must agree, which is
    # what "naive means UTC" means operationally.
    naive, zulu = "2026-03-05T06:07:08", "2026-03-05T06:07:08Z"
    if abs(parser.to_epoch(naive) - parser.to_epoch(zulu)) > 1e-9:
        bad.append(f"to_epoch({naive!r}) and to_epoch({zulu!r}) differ; naive "
                   f"input is documented as UTC")

    # Equal instants written in different offsets must produce equal epochs.
    pairs = [("2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+01:00"),
             ("2026-01-01T00:00:00Z", "2025-12-31T19:00:00-05:00")]
    for left, right in pairs:
        if abs(parser.to_epoch(left) - parser.to_epoch(right)) > 1e-9:
            bad.append(f"to_epoch({left!r}) != to_epoch({right!r}); these are "
                       f"the same instant")

    # Monotonicity: later text, later epoch.
    ordered = ["2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z",
               "2026-01-01T00:01:00Z", "2026-01-02T00:00:00Z",
               "2027-01-01T00:00:00Z"]
    epochs = [parser.to_epoch(t) for t in ordered]
    if epochs != sorted(epochs):
        bad.append(f"epochs are not monotonic for ordered timestamps: {epochs}")
    return bad


INVARIANTS = {
    "A1_crc_telemetry": _inv_a1,
    "A2_sensor_json_logger": _inv_a2,
    "A3_iso_ts_parser": _inv_a3,
    "B1_scada_poller": _inv_b1,
    "B2_modbus_meter_reader": _inv_b2,
    "B3_mqtt_telemetry_bridge": _inv_b3,
    "C1_pid_pump_controller": _inv_c1,
    "C2_tank_level_fsm": _inv_c2,
    "C3_anomaly_threshold_engine": _inv_c3,
    "D1_boiler_control_loop": _inv_d1,
    "D2_pump_station_scheduler": _inv_d2,
    "D3_telemetry_gateway": _inv_d3,
}
