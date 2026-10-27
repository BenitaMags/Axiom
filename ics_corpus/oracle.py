"""Shared oracle machinery for the diversification corpus.

Used by `make_golden.py` (records the reference behavior) and `test_corpus.py`
(checks a possibly-diversified variant against that reference). Everything here
operates on a *module object*, so the same probes run against the pristine
reference and against a diversified variant with the same public API.

Three things live here:
  * builders   -- configs/gains matching each module's run_scenario
  * PROBES     -- deterministic inputs that drive every branch and state
  * INVARIANTS -- properties that must hold for ANY input
  * approx_equal -- nested exact/tolerance comparison for golden checks
"""

import importlib
import math

MODULES = ["pid_governor", "pump_controller", "protective_relay", "sensor_pipeline"]


def load(name):
    return importlib.import_module(name)


def lcg(seed):
    """Deterministic PRNG so 'random' invariant inputs are reproducible."""
    s = seed & 0xFFFFFFFF
    while True:
        s = (1664525 * s + 1013904223) & 0xFFFFFFFF
        yield s / 0xFFFFFFFF


# --------------------------------------------------------------------------
# Config / parameter builders (mirror each module's run_scenario)
# --------------------------------------------------------------------------
def _pid(m):
    return m.PIDGains(0.015, 0.040, 0.0008), 3000.0, 0.05


def _pump_cfg(m):
    return m.PumpConfig(20.0, 80.0, 100.0, 12.0, 40.0)


def _relay_cfg(m):
    return m.RelayConfig(100.0, 800.0, 6.0, 230.0, 0.15, 49.0, 51.0, 1.0, 2)


def _sensor_cfg(m):
    return m.SensorConfig(2.0, -5.0, 0.0, 300.0, 0.8, 8, 20.0, 180.0, 5.0, 230.0)


# --------------------------------------------------------------------------
# Coverage probes -- each returns a list of observable outputs (JSON-able)
# --------------------------------------------------------------------------
def pid_sat_high(m):
    """Huge setpoint error -> fuel command must saturate at the upper clamp."""
    gains, _, dt = _pid(m)
    st = m.GovernorState(0.0, 0.0, 0.0)
    out = []
    for _ in range(15):
        st, cmd = m.governor_step(st, 100000.0, gains, 0.0, dt)
        out.append([st.rpm, cmd])
    return out


def pid_sat_low(m):
    """RPM far above setpoint -> command must saturate at the lower clamp (0)."""
    gains, _, dt = _pid(m)
    st = m.GovernorState(6000.0, 0.0, 0.0)
    out = []
    for _ in range(15):
        st, cmd = m.governor_step(st, 500.0, gains, 0.0, dt)
        out.append([st.rpm, cmd])
    return out


def pump_cycle(m):
    """Drain below low (OFF->ON) then stop demand (ON->OFF)."""
    cfg, dt = _pump_cfg(m), 0.5
    st = m.PumpState(50.0, "OFF", 0.0)
    out = []
    for d in [11.0] * 30 + [0.0] * 40:
        st = m.pump_step(st, d, cfg, dt)
        out.append([st.level, st.mode, st.runtime])
    return out


def pump_fault_overflow(m):
    """Level at overflow -> latching FAULT via the overflow interlock."""
    cfg, dt = _pump_cfg(m), 0.5
    st = m.PumpState(cfg.overflow, "OFF", 0.0)
    st = m.pump_step(st, 0.0, cfg, dt)
    return [[st.level, st.mode, st.runtime]]


def pump_fault_runtime(m):
    """Pump held ON with zero net flow -> FAULT via max-runtime interlock."""
    cfg, dt = _pump_cfg(m), 0.5
    st = m.PumpState(50.0, "ON", 0.0)
    out = []
    for _ in range(100):
        st = m.pump_step(st, cfg.pump_rate, cfg, dt)
        out.append([st.level, st.mode, st.runtime])
    return out


def relay_instantaneous(m):
    """Current above the instantaneous pickup -> immediate trip."""
    cfg, dt = _relay_cfg(m), 0.05
    st = m.RelayState("CLOSED", 0.0, 0.0, 0)
    st = m.relay_step(st, 900.0, 230.0, 50.0, cfg, dt)
    return [[st.mode, st.accum, st.timer, st.reclose_count]]


def relay_inverse_time(m):
    """Moderate overcurrent -> inverse-time accumulator eventually trips."""
    cfg, dt = _relay_cfg(m), 0.05
    st = m.RelayState("CLOSED", 0.0, 0.0, 0)
    out = []
    for _ in range(40):
        st = m.relay_step(st, 200.0, 230.0, 50.0, cfg, dt)
        out.append([st.mode, st.accum, st.reclose_count])
    return out


def relay_overvoltage(m):
    cfg, dt = _relay_cfg(m), 0.05
    st = m.RelayState("CLOSED", 0.0, 0.0, 0)
    st = m.relay_step(st, 60.0, 300.0, 50.0, cfg, dt)
    return [[st.mode]]


def relay_underfreq(m):
    cfg, dt = _relay_cfg(m), 0.05
    st = m.RelayState("CLOSED", 0.0, 0.0, 0)
    st = m.relay_step(st, 60.0, 230.0, 48.0, cfg, dt)
    return [[st.mode]]


def relay_overfreq(m):
    cfg, dt = _relay_cfg(m), 0.05
    st = m.RelayState("CLOSED", 0.0, 0.0, 0)
    st = m.relay_step(st, 60.0, 230.0, 52.0, cfg, dt)
    return [[st.mode]]


def relay_accum_decay(m):
    """Build accumulator above pickup, then drop below it -> accumulator decays."""
    cfg, dt = _relay_cfg(m), 0.05
    st = m.RelayState("CLOSED", 0.0, 0.0, 0)
    for _ in range(10):
        st = m.relay_step(st, 200.0, 230.0, 50.0, cfg, dt)
    peak = st.accum
    for _ in range(10):
        st = m.relay_step(st, 50.0, 230.0, 50.0, cfg, dt)
    return [[peak, st.accum, st.mode]]


def relay_lockout(m):
    """Persistent fault -> CLOSED/TRIPPED/RECLOSING cycles ending in LOCKOUT."""
    cfg, dt = _relay_cfg(m), 0.05
    st = m.RelayState("CLOSED", 0.0, 0.0, 0)
    out = []
    for _ in range(120):
        st = m.relay_step(st, 900.0, 230.0, 50.0, cfg, dt)
        out.append([st.mode])
    return out


def _sensor_steady(m, sample, steps=12):
    cfg = _sensor_cfg(m)
    st = m.SensorState(0.0, [0.0] * cfg.window, 0)
    avg = status = None
    for _ in range(steps):
        st, avg, status = m.process(sample, st, cfg)
    return avg, status


def sensor_clamp_high(m):
    """Raw sample whose calibrated value exceeds range_hi -> clamps down."""
    cfg = _sensor_cfg(m)
    st = m.SensorState(0.0, [0.0] * cfg.window, 0)
    st, _, _ = m.process(200.0, st, cfg)
    return [[st.reported]]


def sensor_clamp_low(m):
    cfg = _sensor_cfg(m)
    st = m.SensorState(0.0, [0.0] * cfg.window, 0)
    st, _, _ = m.process(0.0, st, cfg)
    return [[st.reported]]


def sensor_deadband(m):
    """Two nearby samples -> second is inside the deadband, reported unchanged."""
    cfg = _sensor_cfg(m)
    st = m.SensorState(0.0, [0.0] * cfg.window, 0)
    st, _, _ = m.process(52.5, st, cfg)
    r1 = st.reported
    st, _, _ = m.process(52.7, st, cfg)
    return [[r1, st.reported]]


def sensor_class_normal(m):
    return [list(_sensor_steady(m, 52.5))]


def sensor_class_warn_hi(m):
    return [list(_sensor_steady(m, 102.5))]


def sensor_class_alarm_hi(m):
    return [list(_sensor_steady(m, 127.5))]


def sensor_class_warn_lo(m):
    return [list(_sensor_steady(m, 7.5))]


def sensor_class_alarm_lo(m):
    return [list(_sensor_steady(m, 2.0))]


PROBES = {
    "pid_governor": {
        "sat_high": pid_sat_high,
        "sat_low": pid_sat_low,
    },
    "pump_controller": {
        "cycle": pump_cycle,
        "fault_overflow": pump_fault_overflow,
        "fault_runtime": pump_fault_runtime,
    },
    "protective_relay": {
        "instantaneous": relay_instantaneous,
        "inverse_time": relay_inverse_time,
        "overvoltage": relay_overvoltage,
        "underfreq": relay_underfreq,
        "overfreq": relay_overfreq,
        "accum_decay": relay_accum_decay,
        "lockout": relay_lockout,
    },
    "sensor_pipeline": {
        "clamp_high": sensor_clamp_high,
        "clamp_low": sensor_clamp_low,
        "deadband": sensor_deadband,
        "class_normal": sensor_class_normal,
        "class_warn_hi": sensor_class_warn_hi,
        "class_alarm_hi": sensor_class_alarm_hi,
        "class_warn_lo": sensor_class_warn_lo,
        "class_alarm_lo": sensor_class_alarm_lo,
    },
}


# --------------------------------------------------------------------------
# Invariants -- must hold for ANY input, so they catch variant bugs on inputs
# that the golden vectors never happen to hit.
# --------------------------------------------------------------------------
def inv_pid(m):
    gains, sp, dt = _pid(m)
    checks = []
    rng = lcg(1)
    for _ in range(200):
        x = (next(rng) - 0.5) * 1000.0
        lo = (next(rng) - 0.5) * 100.0
        hi = lo + next(rng) * 100.0 + 1.0
        c = m.clamp(x, lo, hi)
        checks.append(("clamp within bounds", lo - 1e-9 <= c <= hi + 1e-9))
        checks.append(("clamp idempotent", m.clamp(c, lo, hi) == c))
    st = m.GovernorState(0.0, 0.0, 0.0)
    for _ in range(500):
        load = (next(rng) - 0.5) * 3000.0
        st, cmd = m.governor_step(st, sp, gains, load, dt)
        checks.append(("command in [0,100]", -1e-9 <= cmd <= 100.0 + 1e-9))
        checks.append(("integral bounded", -2500.0 - 1e-6 <= st.integral <= 2500.0 + 1e-6))
        checks.append(("rpm finite", math.isfinite(st.rpm)))
    return checks


def inv_pump(m):
    cfg, dt = _pump_cfg(m), 0.5
    checks = []
    rng = lcg(2)
    st = m.PumpState(50.0, "OFF", 0.0)
    faulted = False
    for _ in range(1000):
        st = m.pump_step(st, next(rng) * 20.0 - 3.0, cfg, dt)
        checks.append(("level in [0,overflow]", -1e-9 <= st.level <= cfg.overflow + 1e-9))
        checks.append(("mode valid", st.mode in ("OFF", "ON", "FAULT")))
        checks.append(("runtime nonneg", st.runtime >= 0.0))
        if faulted:
            checks.append(("FAULT latched", st.mode == "FAULT"))
        if st.mode == "FAULT":
            faulted = True
    return checks


def inv_relay(m):
    cfg, dt = _relay_cfg(m), 0.05
    checks = []
    rng = lcg(3)
    valid = {"CLOSED", "TRIPPED", "RECLOSING", "LOCKOUT"}
    st = m.RelayState("CLOSED", 0.0, 0.0, 0)
    locked = False
    for _ in range(2000):
        cur = next(rng) * 1000.0
        volt = 200.0 + next(rng) * 100.0
        freq = 48.0 + next(rng) * 4.0
        st = m.relay_step(st, cur, volt, freq, cfg, dt)
        checks.append(("mode valid", st.mode in valid))
        checks.append(("accum nonneg", st.accum >= -1e-9))
        checks.append(("reclose in range", 0 <= st.reclose_count <= cfg.max_reclose))
        if locked:
            checks.append(("LOCKOUT latched", st.mode == "LOCKOUT"))
        if st.mode == "LOCKOUT":
            locked = True
    return checks


def inv_sensor(m):
    cfg = _sensor_cfg(m)
    checks = []
    rng = lcg(4)
    st = m.SensorState(0.0, [0.0] * cfg.window, 0)
    for _ in range(1000):
        st, avg, status = m.process(next(rng) * 400.0 - 50.0, st, cfg)
        checks.append(("status valid", status in ("NORMAL", "WARN", "ALARM")))
        checks.append(("avg in range", cfg.range_lo - 1e-9 <= avg <= cfg.range_hi + 1e-9))
        checks.append(("buf length const", len(st.buf) == cfg.window))
        checks.append(("idx in range", 0 <= st.idx < cfg.window))
    return checks


INVARIANTS = {
    "pid_governor": inv_pid,
    "pump_controller": inv_pump,
    "protective_relay": inv_relay,
    "sensor_pipeline": inv_sensor,
}


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------
def approx_equal(a, b, tol=0.0):
    """Nested compare. Floats within abs tol; everything else exact.
    tol=0.0 -> bit-exact. Raise tol (e.g. 1e-9) for float-reassociating transforms."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return True
        return abs(a - b) <= tol
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(approx_equal(x, y, tol) for x, y in zip(a, b))
    return a == b
