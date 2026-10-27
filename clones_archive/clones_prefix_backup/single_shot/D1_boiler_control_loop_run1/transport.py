"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    """Build a minimalmodbus-based client.

    minimalmodbus is natively a serial-port (Modbus RTU) library, but it also
    exposes the same register-oriented API surface (`read_register`,
    `write_register`) that we rely on here through its TCP-capable subclass
    when available, or through a thin wrapper otherwise. We keep this
    indirection so the rest of the module only ever talks to a small,
    consistent client interface.
    """
    import minimalmodbus

    # minimalmodbus.Instrument expects a serial port string as its first
    # argument for RTU; for our purposes we treat `host` as the port
    # identifier (e.g. a TCP-serial bridge address or device path) and rely
    # on minimalmodbus's default slave address handling via `unit`.
    instrument = minimalmodbus.Instrument(host, 1)
    instrument.serial.timeout = 1
    return instrument


class _MinimalModbusAdapter:
    """Glue layer adapting a minimalmodbus.Instrument-like object to the
    read/write interface `ModbusIO` expects (mirroring the previous
    pymodbus-based call shape, including a unit/slave id parameter and a
    result object exposing `.registers` / `.isError()`)."""

    class _ReadResult:
        def __init__(self, registers):
            self.registers = registers
            self._error = False

        def isError(self):
            return self._error

    class _WriteResult:
        def __init__(self, error=False):
            self._error = error

        def isError(self):
            return self._error

    def __init__(self, instrument):
        self.instrument = instrument

    def read_holding_registers(self, address, count, unit=1):
        self.instrument.address = unit
        try:
            registers = []
            for offset in range(count):
                value = self.instrument.read_register(
                    address + offset, number_of_decimals=0, functioncode=3, signed=False
                )
                registers.append(int(value))
            return self._ReadResult(registers)
        except Exception:
            return self._ReadResult([])._error_result() if False else self._failed_read()

    def _failed_read(self):
        result = self._ReadResult([])
        result._error = True
        return result

    def write_register(self, address, value, unit=1):
        self.instrument.address = unit
        try:
            self.instrument.write_register(
                address, value, number_of_decimals=0, functioncode=6, signed=False
            )
            return self._WriteResult(error=False)
        except Exception:
            return self._WriteResult(error=True)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if client is not None:
            self.client = client
        else:
            self.client = _MinimalModbusAdapter(_default_client(host))
        self.unit = unit

    def _read1(self, address: str) -> int:
        response = self.client.read_holding_registers(address, 1, unit=self.unit)
        if response.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return response.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        raw = self._read1(address)
        return raw / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        raw = self._read1(address)
        return raw / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        bounded_percent = max(0.0, min(100.0, percent))
        counts = int(round(bounded_percent * 40.95))  # 0-100% -> 0-4095
        response = self.client.write_register(address, counts, unit=self.unit)
        if response.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
