# Proteus Evaluation Corpus

Twelve Python programs from the **industrial measurement and control** domain, used to
evaluate automated code-clone generation. The corpus is organized along two axes:

* **Transformation demand** — three categories that isolate the contribution of each
  Proteus agent:
  * **A. Drop-in library substitution** (exercises the *LSA* only): the replacement is
    API-compatible, so no glue or logic change is needed.
  * **B. Substitution requiring glue** (exercises *LSA + GSA*): the replacement library
    has a different interface, so adapter/glue code is required.
  * **C. Substitution + logic mutation** (exercises *LSA + GSA + LMA*): the substitution
    removes a third-party behavior that must be re-implemented and diversified as
    first-party logic.
  * **D. Combined** (exercises *LSA + GSA + LMA together*): a single realistic program
    that needs an API-incompatible swap, its integration glue, *and* substantial
    first-party control/estimation logic to diversify.
* **Structural complexity** — from a single self-contained module to multi-file packages
  with internal dependencies.

Each program ships a `pytest` suite and (implicitly, via the tests' fixtures) recorded
input/output behavior that serves as the **VCA test corpus** and the **equivalence-oracle
ground truth**. The requirement given to Proteus is the substitution in each
`requirements.txt`, plus *"diversify all first-party logic"* for Category C.

| Dir | Program | Files | Swap (from → to) | Category |
|-----|---------|:----:|------------------|----------|
| `A1_crc_telemetry`          | `crc_telemetry`          | 1 | `zlib.crc32` → `fastcrc`         | A |
| `A2_sensor_json_logger`     | `sensor_json_logger`     | 1 | `json` → `ujson`                | A |
| `A3_iso_ts_parser`          | `iso_ts_parser`          | 1 | `datetime` → `ciso8601`         | A |
| `B1_scada_poller`           | `scada_poller`           | 1 | `requests` → `httpx`            | B |
| `B2_modbus_meter_reader`    | `modbus_meter_reader`    | 2 | `pymodbus` → `minimalmodbus`    | B |
| `B3_mqtt_telemetry_bridge`  | `mqtt_telemetry_bridge`  | 3 | `paho-mqtt` → `aiomqtt`         | B |
| `C1_pid_pump_controller`    | `pid_pump_controller`    | 3 | `simple-pid` → in-house         | C |
| `C2_tank_level_fsm`         | `tank_level_fsm`         | 3 | `transitions` → hand-rolled FSM | C |
| `C3_anomaly_threshold_engine` | `anomaly_threshold_engine` | 4 | `smtplib` notifier → `apprise` | C |
| `D1_boiler_control_loop`    | `boiler_control_loop`    | 3 | `pymodbus` → `minimalmodbus`    | D |
| `D2_pump_station_scheduler` | `pump_station_scheduler` | 3 | `requests` → `httpx`            | D |
| `D3_telemetry_gateway`      | `telemetry_gateway`      | 3 | `paho-mqtt` → `aiomqtt`         | D |

## Design notes

* The programs are the **originals** that Proteus must clone. External I/O
  (HTTP sessions, Modbus/MQTT clients, SMTP, the PID object) is **dependency-injected**,
  so the core logic is testable without network, hardware, or the third-party libraries
  installed. Production code paths still import the real "from" library by default.
* Module basenames are unique across the corpus, so `pytest` can be run from this
  directory over all programs at once.

## Reporting (for collecting experiment results)

`reporting/` is a fill-in-the-blanks kit for recording clone-generation experiments:

* `METRICS_GUIDE.md` — the procedure and every metric defined (CORE vs. extended).
* `results.csv` — pre-populated with one row per program × configuration; fill the metrics.
* `run_worksheet.md` — a per-run template for qualitative notes on noteworthy runs.
* `summarize.py` — `python3 summarize.py` turns the filled CSV into the paper's
  per-configuration averages (generation success, equivalence confirmation, divergence,
  repair iterations, cost).

## Running the tests

```bash
# all programs (requires pytest):
pytest -q

# or without pytest — each test file is dual-mode:
./run_all_tests.sh
```
