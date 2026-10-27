"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _MinimalModbusAdapter:
    """Adapts a minimalmodbus.Instrument to the read_holding_registers/isError API
    used by MeterReader, so the rest of the code can stay pymodbus-shaped.
    """

    def __init__(self, instrument):
        self._instrument = instrument

    def read_holding_registers(self, address, count, unit=1):
        self._instrument.address = unit
        try:
            registers = self._instrument.read_registers(
                address, count, functioncode=3
            )
            return _Result(registers=registers, error=False)
        except Exception:
            return _Result(registers=None, error=True)


class _Result:
    def __init__(self, registers, error):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


def _default_client(host: str, port: int = 502):
    import minimalmodbus  # lazy import for testability

    # minimalmodbus expects a serial port string as its first argument, but for
    # networked meters it is commonly used with a TCP-to-serial bridge address
    # string of the form "host:port" via a suitable serial-over-TCP layer.
    instrument = minimalmodbus.Instrument(f"{host}:{port}", 1)
    return _MinimalModbusAdapter(instrument)


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
