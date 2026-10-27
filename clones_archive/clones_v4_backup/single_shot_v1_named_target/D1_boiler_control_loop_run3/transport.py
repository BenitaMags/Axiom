"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    """Build a minimalmodbus-backed client wrapped to look like our expected client.

    minimalmodbus is natively a serial-port (RTU) library; when it is used with a
    TCP-capable subclass (e.g. minimalmodbus.Instrument configured for a TCP bridge,
    or a user-provided compatible object) `host` is passed through as the "port"
    argument. We lazily import here to keep this module importable without the
    dependency installed, and to keep tests able to inject fakes easily.
    """
    import minimalmodbus  # lazy import for testability

    instrument = minimalmodbus.Instrument(host, 1)
    return instrument


class _MinimalModbusAdapter:
    """Adapts a minimalmodbus.Instrument-like object to the read/write shape
    used internally by ModbusIO (register-count reads, error-raising writes).
    """

    def __init__(self, instrument):
        self._instrument = instrument

    def read_holding_registers(self, address, count=1, unit=1):
        try:
            value = self._instrument.read_register(address, number_of_decimals=0,
                                                     functioncode=3)
        except Exception as exc:  # noqa: BLE001 - normalize any backend error
            return _ErrorResult(str(exc))
        return _OkResult([int(value)])

    def write_register(self, address, value, unit=1):
        try:
            self._instrument.write_register(address, value, number_of_decimals=0,
                                             functioncode=6)
        except Exception as exc:  # noqa: BLE001 - normalize any backend error
            return _ErrorResult(str(exc))
        return _OkResult([value])


class _OkResult:
    def __init__(self, registers):
        self.registers = registers

    def isError(self):
        return False


class _ErrorResult:
    def __init__(self, message):
        self.message = message
        self.registers = []

    def isError(self):
        return True


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if client is not None:
            self.client = client
        else:
            raw_instrument = _default_client(host)
            self.client = _MinimalModbusAdapter(raw_instrument)
        self.unit = unit

    def _read1(self, address: int) -> int:
        result = self.client.read_holding_registers(address, 1, unit=self.unit)
        if result.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return result.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        raw = self._read1(address)
        return raw / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        raw = self._read1(address)
        return raw / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        bounded_percent = max(0.0, min(100.0, percent))
        counts = int(round(bounded_percent * 40.95))  # 0-100% -> 0-4095
        result = self.client.write_register(address, counts, unit=self.unit)
        if result.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
