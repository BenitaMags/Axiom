"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.

This version uses `minimalmodbus` as the underlying transport. Since
`minimalmodbus.Instrument.read_registers()` returns a plain list of ints (and raises
an exception on error instead of returning an error-flagged response object), a small
adapter (`_MinimalModbusClientAdapter`) is used to expose the same
`read_holding_registers(address, count, unit=...)` -> object-with-`.registers`/
`.isError()` interface that the rest of this module (and the test suite) expects.
"""
from registers import REGISTERS, decode


class _ReadResult:
    """Adapter result object mimicking pymodbus's response interface.

    Exposes `.registers` (list[int]) and `.isError()` so callers written against the
    pymodbus API continue to work unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _MinimalModbusClientAdapter:
    """Adapts a `minimalmodbus.Instrument` to the pymodbus-style client interface
    used by `MeterReader` (i.e. `read_holding_registers(address, count, unit=...)`
    returning an object with `.registers` / `.isError()`).
    """

    def __init__(self, instrument):
        self._instrument = instrument

    def read_holding_registers(self, address, count, unit=1):
        try:
            self._instrument.address = unit
            words = self._instrument.read_registers(
                registeraddress=address,
                number_of_registers=count,
                functioncode=3,
            )
            return _ReadResult(registers=list(words), error=False)
        except Exception:
            return _ReadResult(registers=[], error=True)


def _default_client(host: str, port: int = 502):
    import minimalmodbus  # lazy import for testability

    instrument = minimalmodbus.Instrument(host, 1)
    return _MinimalModbusClientAdapter(instrument)


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self.client.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
