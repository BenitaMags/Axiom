"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomHoldingRegistersResult:
    """Adapter object emulating a pymodbus read_holding_registers() response.

    pymodbus's read_holding_registers() returns a response object exposing
    `.registers` (the list of register values) and `.isError()` (a bool-returning
    method indicating whether the read failed). minimalmodbus's
    Instrument.read_registers() instead simply returns a raw list of register
    values on success, and raises an exception (e.g. IOError, ValueError,
    minimalmodbus.ModbusException) on failure -- it has no result object at all.

    This adapter stores the raw register list (or None on failure) plus a
    captured error flag, and exposes `.registers` and `.isError()` so existing
    call sites written against the pymodbus-style API keep working unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


def _axiom_read_holding_registers_wrapper(instrument, address, count, unit=None):
    """Wrap minimalmodbus.Instrument.read_registers() to look like pymodbus's
    read_holding_registers(address, count, unit=...).

    minimalmodbus's read_registers(registeraddress, number_of_registers,
    functioncode=3) reads holding registers by default and returns a plain
    list of ints, raising an exception if the read fails; it has no `unit`
    keyword (the slave address is configured on the Instrument itself, not
    passed per-call). pymodbus's read_holding_registers(address, count,
    unit=...) instead returns a response object with `.registers`/.isError()`
    and never raises for a failed read.

    This wrapper calls minimalmodbus's read_registers(), catches any
    exception to emulate `.isError()` returning True, and packages the raw
    list (or empty list on failure) into an _AxiomHoldingRegistersResult so
    the call site can keep using `result.isError()` / `result.registers`.
    """
    try:
        registers = instrument.read_registers(address, count, functioncode=3)
        return _AxiomHoldingRegistersResult(registers=registers, error=False)
    except Exception:
        return _AxiomHoldingRegistersResult(registers=[], error=True)


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = ModbusTcpClient(host, port=port)
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
            result = _axiom_read_holding_registers_wrapper(self.client, address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
