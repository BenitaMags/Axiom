"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient interface to pyModbusTCP's ModbusClient.

    pymodbus's ModbusTcpClient exposes a `.connect()` method used to open the
    TCP connection to the device. pyModbusTCP's `ModbusClient` instead
    exposes an `.open()` method for the same purpose and has no `.connect()`
    method at all. This wrapper constructs a `pyModbusTCP.client.ModbusClient`
    and attaches a `.connect` attribute on the instance that calls `.open()`,
    so downstream code written against the pymodbus-style `.connect()` call
    keeps working unchanged, and the returned object presents a
    pymodbus-like client interface.
    """
    from pyModbusTCP.client import ModbusClient

    client = ModbusClient(host, port=port)
    client.connect = client.open
    return client


class _AxiomModbusResult:
    """Adapt pyModbusTCP's plain list/None result to pymodbus's response API.

    pymodbus's `read_holding_registers` returns a response object exposing
    `.isError()` (False on success) and `.registers` (the list of register
    values read). pyModbusTCP's `ModbusClient.read_holding_registers` instead
    returns the raw list of register values directly on success, or `None`
    if the read failed. This class wraps that raw result so call sites
    written against pymodbus's response API (`.isError()` / `.registers`)
    keep working unchanged.
    """

    def __init__(self, registers):
        self._failed = registers is None
        self.registers = registers if registers is not None else []

    def isError(self):
        return self._failed


def _axiom_read_holding_registers_wrapper(client, address, count, unit=None):
    """Bridge pymodbus's read_holding_registers(unit=...) call to pyModbusTCP.

    pymodbus's `client.read_holding_registers` accepts a `unit` keyword
    argument identifying the target slave/unit id for the request, and
    returns a response object with `.isError()` / `.registers`. pyModbusTCP's
    `ModbusClient` has no per-call `unit` argument; instead the unit id is a
    property (`unit_id`) set on the client before issuing a request, and
    `read_holding_registers(address, count)` returns a plain list of
    register values (or `None` on failure). This wrapper sets the client's
    `unit_id` from the `unit` kwarg (when supported), performs the read
    using pyModbusTCP's positional-only signature, and wraps the raw result
    in `_AxiomModbusResult` so existing `.isError()` / `.registers` call
    sites keep working unchanged.
    """
    if unit is not None and hasattr(client, "unit_id"):
        client.unit_id = unit
    registers = client.read_holding_registers(address, count)
    return _AxiomModbusResult(registers)

def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_wrapper(host, port=port)
    client.connect()
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = _axiom_read_holding_registers_wrapper(
                self.client, address, count, unit=self.unit
            )
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
