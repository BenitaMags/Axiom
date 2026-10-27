"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = ModbusTcpClient(host, port=port)
    client.connect()
    return client


class _AxiomModbusReadResult:
    """Adapter result object mimicking pymodbus's read response contract.

    pymodbus's read_holding_registers() returns a response object that exposes
    `.isError()` (a method returning True on failure) and `.registers` (the
    list of decoded register values) on success. minimalmodbus's
    read_registers() instead returns a plain list of register values directly,
    and signals failure by *raising* an exception rather than returning an
    error-flagged object. This tiny holder lets call sites keep using the
    `.isError()` / `.registers` pattern unchanged regardless of which backend
    is in use.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


def _axiom_read_holding_registers_wrapper(client, address, count, unit=None):
    """Bridge pymodbus-style read_holding_registers(address, count, unit=...)
    calls onto minimalmodbus's instrument.read_registers(address, count) API.

    pymodbus: client.read_holding_registers(address, count, unit=UNIT) returns
    a response object with `.isError()` and `.registers`.

    minimalmodbus: instrument.read_registers(registeraddress, numberOfRegisters)
    returns a plain list of ints directly, and raises exceptions (e.g.
    IOError, ValueError, minimalmodbus.NoResponseError/InvalidResponseError)
    instead of returning an error-flagged object.

    This wrapper calls the minimalmodbus API, validates `unit` (if provided)
    against the instrument's own bound slave `address` attribute (minimalmodbus
    instruments are bound to a single slave address at construction time, so
    there's no per-call unit parameter to pass), and catches any exception
    raised during the read -- translating it into an `_AxiomModbusReadResult`
    with `.isError()` returning True. On success it wraps the returned list in
    an `_AxiomModbusReadResult` so downstream code can keep using
    `result.isError()` / `result.registers` unchanged.
    """
    if unit is not None and hasattr(client, "address") and client.address != unit:
        return _AxiomModbusReadResult(error=True)
    try:
        registers = client.read_registers(address, count)
    except Exception:
        return _AxiomModbusReadResult(error=True)
    return _AxiomModbusReadResult(registers=registers, error=False)


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
