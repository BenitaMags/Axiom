"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.

This module adapts a `minimalmodbus.Instrument`-like client (or any client exposing
`read_registers(address, count, functioncode=...)`) to the small pymodbus-like
interface (`read_holding_registers(address, count, unit=...)` returning an object
with `.registers` and `.isError()`) that `MeterReader` uses internally.
"""
from registers import REGISTERS, decode


def _default_client(host: str, port: int = 502):
    import minimalmodbus  # lazy import for testability

    # minimalmodbus.Instrument expects a serial port name as first argument, but it
    # can also be constructed against a pre-configured pyserial-like port; here we
    # treat `host` as the port identifier (e.g. a TCP-serial bridge device string)
    # to keep the public API (host, port) unchanged while adapting to minimalmodbus.
    instrument = minimalmodbus.Instrument(host, 1)
    return _MinimalModbusAdapter(instrument)


class _ReadResult:
    """Adapter result object mimicking pymodbus's response API."""

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _MinimalModbusAdapter:
    """Wraps a minimalmodbus Instrument to expose a pymodbus-like read method."""

    def __init__(self, instrument):
        self.instrument = instrument

    def read_holding_registers(self, address, count, unit=1):
        try:
            self.instrument.address = unit
            registers = self.instrument.read_registers(
                address, count, functioncode=3
            )
            return _ReadResult(registers=registers, error=False)
        except Exception:
            return _ReadResult(registers=[], error=True)


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
