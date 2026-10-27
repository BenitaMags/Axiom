"""Layer 6 — call the program's own API with the REAL collaborator.

Why layer 5 was not enough
──────────────────────────
``factory_check`` executes the ``_default_*`` factory and classifies what it
raises. That catches a factory which cannot be constructed, and it caught
``NameError: name 'requests' is not defined`` on a D2 clone. It misses every
defect that lives one call later, because constructing an object proves only
that the constructor works:

    C3   ``_default_smtp`` returns a ``redmail.EmailSender`` quite happily.
         ``EmailNotifier.notify`` then calls ``.sendmail(...)`` on it, which
         redmail does not have. Layer 5 reported OK.
    B2   ``_default_client`` builds a ``pyModbusTCP.ModbusClient`` fine.
         ``read_measurement`` then calls ``read_holding_registers(addr, n,
         unit=...)``, and pyModbusTCP's signature is ``(reg_addr, reg_nb)``.
         Layer 5 reported OK.
    D3   ``_default_client`` reaches its wrapper, which does ``import _Result``
         -- a module that does not exist. Layer 5 looked only at factories
         importing the target library and skipped it.

All three pass their suites, because the suites inject a fake whose signature
matches the ORIGINAL library. The migrated code calls the fake correctly and the
real library incorrectly, and nothing in the corpus ever notices.

A blind LLM review found all three. This module is the deterministic version of
that finding: it constructs each program the way the program's own docstring
says it is used -- with nothing injected -- and performs one real operation.

Classification is delegated to ``factory_check``, which already distinguishes
"this call is wrong" from "there is no server here". Only MISUSE fails. An
absent broker, gateway or meter is expected and is never a defect: every
operation below points at 192.0.2.1, the RFC 5737 documentation address, which
is guaranteed not to route.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from factory_check import (  # noqa: E402
    PROBE_HOST, _MISUSE_TYPES, _UNREACHABLE_SIGNS, _known,
)

#: Operations per program, in the program's own vocabulary.
#:
#: Each entry is a list of ``(label, body)``. Every body must construct the
#: subject WITHOUT injecting a collaborator and call one public method. The
#: constructor argument that would normally receive the fake is simply omitted,
#: which is what makes the ``_default_*`` factory run.
#:
#: WHY MORE THAN ONE PER PROGRAM
#: This was a single operation per program, and that was not enough. D1's only
#: operation was ``read_temperature``, so the READ path was exercised and the
#: WRITE path never was -- and a D1 clone shipped with
#: ``_axiom_write_register_wrapper`` using ``defines`` and ``mt_exceptions``
#: without importing either, so every valve write raised NameError. That clone
#: scored migration_success=Y on all seven layers. A blind LLM judge found it
#: by reading the code; nothing here did, because nothing here called the
#: method.
#:
#: So a program contributes an operation for each public method that reaches
#: the collaborator, plus the top-level orchestrator where one exists, since
#: that drives read and write in the order the program actually uses them.
#:
#: A and A-like programs are absent on purpose: they have no injection seam, so
#: their own tests already execute the migrated call and layers 1-4 cover them.
OPERATIONS: dict[str, list] = {
    "B1_scada_poller": [
        ("read_tag", """
from scada_poller import ScadaPoller
ScadaPoller(f"http://{HOST}:9", timeout=2.0).read_tag("PT101")
"""),
        ("poll", """
from scada_poller import ScadaPoller
ScadaPoller(f"http://{HOST}:9", timeout=2.0).poll(["PT101", "TT200"])
"""),
    ],
    "B2_modbus_meter_reader": [
        ("read_measurement", """
from modbus_meter_reader import MeterReader
MeterReader(host=HOST).read_measurement()
"""),
    ],
    "B3_mqtt_telemetry_bridge": [
        ("publish_reading", """
from mqtt_config import BridgeConfig
from bridge import TelemetryBridge
cfg = BridgeConfig()
for field in ("host", "broker", "hostname", "broker_host"):
    if hasattr(cfg, field):
        setattr(cfg, field, HOST)
TelemetryBridge(cfg).publish_reading("PT101", {"value": 42.0, "ts": "t1"})
"""),
        ("publish_batch", """
from mqtt_config import BridgeConfig
from bridge import TelemetryBridge
cfg = BridgeConfig()
for field in ("host", "broker", "hostname", "broker_host"):
    if hasattr(cfg, field):
        setattr(cfg, field, HOST)
TelemetryBridge(cfg).publish_batch({"PT101": {"value": 42.0, "ts": "t1"}})
"""),
    ],
    "C2_tank_level_fsm": [
        ("step", """
from fsm import TankController
TankController().step(5.0)
"""),
    ],
    "C3_anomaly_threshold_engine": [
        ("notify", """
from notifier import EmailNotifier
EmailNotifier("a@example.invalid", ["b@example.invalid"]).notify("subj", "body")
"""),
        # The engine takes a {tag: TagConfig} dict from engine_config.load() and
        # an OPTIONAL notifier; process() takes one {"tag", "value"} dict. An
        # earlier version of this probe imported an `EngineConfig` that the
        # program does not define, passed process() two positional arguments,
        # and never handed the engine a notifier -- so it failed on the
        # ORIGINAL program, and because the baseline was then decided per
        # program, that one broken probe excused the real `notify` defect on
        # every C3 clone. The notifier is built with nothing injected so the
        # engine's state change reaches the migrated transport.
        ("engine_process", """
from engine import AnomalyEngine
from engine_config import load
from notifier import EmailNotifier
config = load({"tags": [{"tag": "PT101", "high": 8.0, "low": 2.0}]})
notifier = EmailNotifier("a@example.invalid", ["b@example.invalid"])
AnomalyEngine(config, notifier=notifier).process({"tag": "PT101", "value": 999.0})
"""),
    ],
    "D1_boiler_control_loop": [
        ("read_temperature", """
from transport import ModbusIO
ModbusIO(host=HOST).read_temperature()
"""),
        # The operation that was missing. A clone whose write wrapper omitted
        # its imports passed every layer because nothing ever called this.
        ("write_valve", """
from transport import ModbusIO
ModbusIO(host=HOST).write_valve(50.0)
"""),
        ("supervisor_cycle", """
from transport import ModbusIO
from supervisor import BoilerSupervisor
BoilerSupervisor(ModbusIO(host=HOST)).cycle()
"""),
    ],
    "D2_pump_station_scheduler": [
        ("get_demand", """
from client import ScadaClient
ScadaClient(f"http://{HOST}:9", timeout=2.0).get_demand()
"""),
        # POST, not GET: a different verb through the same session, and the
        # only operation that sends a body.
        ("command", """
from client import ScadaClient
ScadaClient(f"http://{HOST}:9", timeout=2.0).command(1, True)
"""),
        ("get_runtimes", """
from client import ScadaClient
ScadaClient(f"http://{HOST}:9", timeout=2.0).get_runtimes()
"""),
    ],
    "D3_telemetry_gateway": [
        ("publish", """
from sink import MqttSink
MqttSink().publish("PT101", {"value": 1.0})
"""),
        ("gateway_process", """
from sink import MqttSink
from gateway import TelemetryGateway
TelemetryGateway(reader=lambda tag: 100, sink=MqttSink()).process("PT101")
"""),
    ],
}


_RUNNER = r'''
import json, os, socket, sys, threading, traceback
os.chdir({clone!r})
sys.path.insert(0, {clone!r})

# A local socket that ACCEPTS and then says nothing.
#
# The probe address used to be 192.0.2.1, which does not route, so every
# connecting constructor blocked until the timeout and the operation never
# reached the method under test. D1 is the proof: its only operation was
# ``read_temperature``, and ``ModbusIO(host=...)`` hung in ``_default_client``
# before a single line of the migrated read path ran. The layer reported
# UNREACHABLE, which reads as "fine", for a program it had never exercised.
#
# Accepting the connection lets construction succeed, so the call actually
# happens. The server never replies, so any real I/O still ends in a timeout
# rather than a fabricated success -- an operation that needs a genuine
# protocol response is still UNREACHABLE, which is correct. What changes is
# that everything BEFORE the response -- argument evaluation, name lookups,
# signature matching -- now runs, and that is where migration defects live.
_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
_srv.bind(("127.0.0.1", 0))
_srv.listen(8)
_HELD = []

def _accept_forever():
    while True:
        try:
            conn, _ = _srv.accept()
            _HELD.append(conn)          # hold it open; never reply
        except OSError:
            return

threading.Thread(target=_accept_forever, daemon=True).start()
HOST = "127.0.0.1"
PORT = _srv.getsockname()[1]
try:
{body}
    print(json.dumps({{"status": "OK", "etype": "", "origin": "", "detail": ""}}))
except BaseException as exc:
    # Which first-party file the failure actually came from. The verifier runs
    # per file, so a project that is half migrated would otherwise report a
    # defect caused by a sibling nobody has touched yet and burn a repair cycle
    # on the wrong file. The DEEPEST frame inside the clone is the one that
    # made the bad call.
    origin = ""
    for frame in reversed(traceback.extract_tb(exc.__traceback__) or []):
        fn = os.path.abspath(frame.filename)
        if fn.startswith(os.path.abspath({clone!r})) and os.path.basename(fn) != "<string>":
            origin = os.path.basename(fn)
            break
    print(json.dumps({{"status": "RAISED",
                      "etype": type(exc).__name__,
                      "origin": origin,
                      "detail": str(exc)[:400] or repr(exc)[:400]}}))
'''


def _classify(record: dict, source_lib: str, target_lib: str) -> str:
    """Identical rules to layer 5, so the two layers cannot disagree."""
    if record.get("status") != "RAISED":
        return record.get("status", "SKIPPED")
    etype = record.get("etype", "")
    detail = record.get("detail", "")
    low = detail.lower()

    if etype in ("ModuleNotFoundError", "ImportError"):
        missing = ""
        if "No module named" in detail:
            missing = detail.split("No module named", 1)[1].strip().strip("'\"")
        return "SKIPPED" if _known(missing, source_lib, target_lib) else "MISUSE"
    if etype in _MISUSE_TYPES:
        return "MISUSE"
    if any(sign in low for sign in _UNREACHABLE_SIGNS):
        return "UNREACHABLE"
    if PROBE_HOST in detail and ("no such file" in low or "could not open" in low):
        return "MISUSE"
    return "UNREACHABLE"


def _run_one(clone_dir: str, body: str, source_lib: str, target_lib: str,
             timeout: int) -> tuple[str, str, str]:
    """``(verdict, detail, origin)`` for a single operation body."""
    indented = "\n".join("    " + line for line in body.strip().splitlines())
    script = _RUNNER.format(clone=os.path.abspath(clone_dir), host=PROBE_HOST,
                            body=indented)
    try:
        proc = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # A blocked call is a real connection attempt against an address that
        # does not route. Well-formed, and not a defect.
        return "UNREACHABLE", f"operation blocked for {timeout}s", ""

    lines = (proc.stdout or "").strip().splitlines()
    if not lines:
        return "SKIPPED", (proc.stderr or "no output")[:200], ""
    try:
        record = json.loads(lines[-1])
    except json.JSONDecodeError:
        return "SKIPPED", lines[-1][:200], ""

    verdict = _classify(record, source_lib, target_lib)
    detail = (f"{record.get('etype')}: {record.get('detail')}"
              if record.get("status") == "RAISED" else "constructed and called")
    return verdict, detail[:220], record.get("origin", "")



def check_each(program_dir: str, program_name: str, source_lib: str = "",
               target_lib: str = "", timeout: int = 40) -> list[tuple]:
    """``[(label, verdict, detail, origin), ...]`` -- one entry per operation.

    ``check`` collapses a program to a single verdict, which is what the
    Verifier wants. Deciding whether a clone's failure is its own needs the
    finer grain: the comparison has to be made operation by operation, against
    the original's result for THAT operation.
    """
    out = []
    for label, body in OPERATIONS.get(program_name, []):
        verdict, detail, origin = _run_one(program_dir, body, source_lib,
                                           target_lib, timeout)
        out.append((label, verdict, detail, origin))
    return out


def _normalise(detail: str, *dirs: str) -> str:
    """Remove directory paths so the same failure reads the same from two dirs.

    An ImportError names the file it looked in, and that path differs between
    the clone and the original even when the failure is identical.
    """
    for d in dirs:
        if d:
            detail = detail.replace(os.path.abspath(d), "<dir>")
    return detail.strip()


def compare(clone_ops: list[tuple], original_ops: list[tuple],
            clone_dir: str = "", original_dir: str = "") -> tuple[str, str]:
    """``(verdict, detail)`` for a clone, judged operation by operation.

        DEFECT    some operation is MISUSE on the clone and the original does
                  NOT fail that operation in exactly the same way
        BASELINE  every MISUSE on the clone is reproduced, character for
                  character, by the original on the same operation -- the
                  failure is the environment's, not the migration's
        OK        no operation misused the library

    Exact matching is the point. B2 single_shot_repair run 2 raised
    ``ModbusClient.read_holding_registers() got an unexpected keyword argument
    'unit'`` -- pyModbusTCP, called with pymodbus's keyword -- while the
    original raised ``ModbusClientMixin.read_holding_registers() ...`` because
    the INSTALLED pymodbus renamed that keyword. Same exception type, near-
    identical text, different library. Matching on type, or on "the original
    also failed somewhere", excused a clone the blind judge called BROKEN.
    """
    original = {label: (verdict, detail) for label, verdict, detail, _ in original_ops}
    excused = []
    for label, verdict, detail, _origin in clone_ops:
        if verdict != "MISUSE":
            continue
        base_verdict, base_detail = original.get(label, ("", ""))
        if (base_verdict == "MISUSE"
                and _normalise(detail, clone_dir, original_dir)
                == _normalise(base_detail, clone_dir, original_dir)):
            excused.append(f"[{label}] {base_detail}")
            continue
        return "DEFECT", f"[{label}] {detail}"[:220]
    if excused:
        return "BASELINE", (f"original fails identically ({excused[0][:90]}); "
                            f"subject not checkable here")
    results = [(label, v, d) for label, v, d, _ in clone_ops]
    for label, v, d in results:
        if v in ("OK", "UNREACHABLE"):
            return "OK", f"[{label}] {d}"[:220]
    if results:
        label, v, d = results[0]
        return v, f"[{label}] {d}"[:220]
    return "SKIPPED", "no operation registered for this program"


def check(clone_dir: str, program_name: str, source_lib: str = "",
          target_lib: str = "", timeout: int = 40,
          with_origin: bool = False) -> tuple:
    """``(verdict, detail)`` for one clone. OK / MISUSE / UNREACHABLE / SKIPPED.

    Every registered operation runs, and MISUSE on ANY of them is the verdict
    for the clone. Stopping at the first non-MISUSE result would reproduce the
    defect this layer was extended to catch: D1's read path was fine and its
    write path raised NameError on every call, and because only ``read_temperature``
    was registered, the clone passed all seven layers.

    Only MISUSE is a defect. UNREACHABLE means the call was well-formed and
    there was nothing at the other end, which is the expected outcome for a
    correct network migration run without servers.

    ``target_lib`` must be the corpus's DECLARED target, never the target
    discovered from the clone. ``chosen_target`` derives its answer from imports
    present in the clone and absent from the original, so on a D3 clone that did
    ``import _Result`` -- a module that does not exist -- it returned
    ``_Result``. Passing that through let the invented module answer "is this
    library one we expect to be missing?" with "yes, it is me", and a
    ModuleNotFoundError naming it was excused as an environment fact. An
    invented dependency must never be able to launder itself this way, so the
    declared target is the only value this may be given.
    """
    operations = OPERATIONS.get(program_name)
    if not operations:
        out = ("SKIPPED", "no operation registered for this program", "")
        return out if with_origin else out[:2]
    if not os.path.isdir(clone_dir):
        out = ("SKIPPED", "clone directory missing", "")
        return out if with_origin else out[:2]

    best: tuple | None = None
    for label, body in operations:
        verdict, detail, origin = _run_one(clone_dir, body, source_lib,
                                           target_lib, timeout)
        if verdict == "MISUSE":
            out = ("MISUSE", f"[{label}] {detail}", origin)
            return out if with_origin else out[:2]
        # Prefer a real result over SKIPPED when reporting a clean clone, so a
        # program whose first operation could not run is not described by it.
        if best is None or (best[0] == "SKIPPED" and verdict != "SKIPPED"):
            best = (verdict, f"[{label}] {detail}", origin)
    out = best or ("SKIPPED", "no operation produced a result", "")
    return out if with_origin else out[:2]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Run one program's API against the "
                                             "real (non-injected) collaborator.")
    ap.add_argument("clone_dir")
    ap.add_argument("program")
    ap.add_argument("--source", default="")
    ap.add_argument("--target", default="")
    args = ap.parse_args()
    v, d = check(args.clone_dir, args.program, args.source, args.target)
    print(f"{v}\t{d}")
    raise SystemExit(1 if v == "MISUSE" else 0)


def regression(clone_dir: str, program_dir: str, program_name: str,
               source_lib: str = "", target_lib: str = "") -> tuple:
    """``(verdict, detail)`` comparing a clone against the PRISTINE original.

    A bare MISUSE cannot be read as a defect, because the original may fail the
    same way. Measured here: the corpus's own B2 and D1 raise ``TypeError:
    ModbusClientMixin.read_holding_registers() got an unexpected keyword
    argument 'unit'`` against the installed pymodbus 3.15, which renamed that
    parameter to ``slave``. The corpus was written against an older release, so
    those two programs do not run on this machine before any migration happens.

    Failing a clone for reproducing the original's own behaviour would reject a
    perfect copy -- the same error an early C1 invariant made by asserting a
    clamp the pristine program never performed. So the comparison is
    differential, exactly like the Verifier's baseline-versus-patched static
    analysis:

        DEFECT     the clone is MISUSE and the original is not
        BASELINE   both are MISUSE; the program is broken here to begin with,
                   so this subject cannot be checked on this machine
        OK         the clone constructed and called, or failed only to reach a
                   server that is not there

    Returns the original's verdict in the detail whenever it matters, so a
    BASELINE result names what is wrong with the environment rather than hiding
    it.
    """
    if program_name not in OPERATIONS:
        return "SKIPPED", "no operation registered for this program"
    clone_ops = check_each(clone_dir, program_name, source_lib, target_lib)
    if not any(v == "MISUSE" for _, v, _, _ in clone_ops):
        return compare(clone_ops, [], clone_dir, program_dir)
    original_ops = check_each(program_dir, program_name, source_lib, target_lib)
    return compare(clone_ops, original_ops, clone_dir, program_dir)
