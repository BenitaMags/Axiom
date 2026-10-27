"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: pymodbus → pyModbusTCP
# The following functions bridge API incompatibilities between
# pymodbus and pyModbusTCP. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def create_modbus_client(host: str, port: int = 502):
    """Original: `ModbusTcpClient(host, port=port)` (pymodbus) constructs a
    pymodbus.client.ModbusTcpClient instance which is later connected via
    `.connect()` and read via `.read_holding_registers(address, count, unit=...)`,
    returning a response object exposing `.isError()`/`.registers`.

    New: pyModbusTCP.client.ModbusClient is constructed similarly from
    `host`/`port`, but exposes `.open()` instead of `.connect()` and its
    `read_holding_registers(address, count)` method returns a raw list of
    register values (or None on failure) rather than a response object.

    This wrapper performs the drop-in construction (with `auto_open=False`
    so the caller can still explicitly open the connection, mirroring the
    original two-step connect flow) and returns the new client instance so
    it can be assigned exactly as the original `ModbusTcpClient(...)` call
    was.
    """
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=False)
    return client


def open_modbus_client(client):
    """Original: `client.connect()` (pymodbus) opens the TCP connection on a
    pymodbus.client.ModbusTcpClient instance and returns True/False
    indicating success.

    New: pyModbusTCP.client.ModbusClient uses `.open()` for the same
    purpose, also returning a boolean success indicator.

    This wrapper simply invokes `.open()` on the given pyModbusTCP client
    and returns its boolean result, so the call site can keep the same
    single-call shape as the original `client.connect()` invocation.
    """
    return client.open()


class _CompatModbusResult:
    """Lightweight stand-in for pymodbus's holding-register response object.

    Exposes `.registers` (the raw list of register values, or None on
    failure) and `.isError()` so existing code written against pymodbus's
    response object (`result.isError()`, `result.registers`) keeps working
    unchanged against the new backend.
    """

    def __init__(self, registers):
        self.registers = registers

    def isError(self):
        return self.registers is None


def read_holding_registers_compat(client, address, count, unit=1):
    """Original: `self.client.read_holding_registers(address, count, unit=self.unit)`
    (pymodbus) returns a response object exposing `.isError()` and
    `.registers`, with the target unit/slave id passed as a `unit` keyword
    argument on each call.

    New: `pyModbusTCP.client.ModbusClient.read_holding_registers(address, count)`
    takes no `unit` keyword argument -- the unit/slave id must instead be set
    via the client's `unit_id` attribute beforehand -- and it returns either a
    raw list of register values on success or `None` on failure, instead of a
    response object.

    This wrapper sets `client.unit_id = unit`, performs the read against the
    new API, and wraps the raw list (or None) in a `_CompatModbusResult`
    object exposing `.registers` and `.isError()`, so downstream code
    (`result.isError()`, `result.registers`) continues to function without
    any further modification.
    """
    client.unit_id = unit
    registers = client.read_holding_registers(address, count)
    return _CompatModbusResult(registers)




def _default_client(host: str, port: int = 502):

    client = create_modbus_client(host, port=port)
    open_modbus_client(client)
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = read_holding_registers_compat(self.client, address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record