"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus`. The
minimalmodbus API is serial-first and register-oriented (no explicit error objects,
just raised exceptions), so a small adapter shim is provided to keep the same
dependency-injected `client` shape (an object exposing `read_holding_registers` and
`write_register`, each returning something with an `.isError()` method / raising on
failure) that the rest of this module and the test suite expect.
"""


class _MinimalModbusResult:
    """Adapter result object mimicking the pymodbus response shape.

    Wraps either a successful outcome (registers / written value) or an error,
    exposing an `isError()` method as the rest of the code expects.
    """

    def __init__(self, registers=None, error=None):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


class _MinimalModbusClientAdapter:
    """Bridges minimalmodbus.Instrument to the read/write API used here.

    minimalmodbus talks over a serial link identified by a port string and a
    slave address, rather than TCP host/port; the `host` value is reused as the
    serial port identifier so the public constructor signature of ModbusIO is
    unchanged.
    """

    def __init__(self, port_or_host, slave_address=1, baudrate=9600):
        import minimalmodbus

        self._instrument = minimalmodbus.Instrument(port_or_host, slave_address)
        self._instrument.serial.baudrate = baudrate

    def read_holding_registers(self, address, count=1, unit=1):
        self._instrument.address = unit
        try:
            values = []
            for offset in range(count):
                values.append(
                    self._instrument.read_register(address + offset, 0, functioncode=3)
                )
            return _MinimalModbusResult(registers=values)
        except Exception as exc:  # noqa: BLE001 - broad by design, mirrors pymodbus errors
            return _MinimalModbusResult(error=exc)

    def write_register(self, address, value, unit=1):
        self._instrument.address = unit
        try:
            self._instrument.write_register(address, value, 0, functioncode=6)
            return _MinimalModbusResult(registers=[value])
        except Exception as exc:  # noqa: BLE001
            return _MinimalModbusResult(error=exc)


def _default_client(host: str, port: int = 502):
    # `port` retained for signature compatibility; minimalmodbus is serial-based
    # and does not use a TCP port, so it is accepted but unused here.
    return _MinimalModbusClientAdapter(host)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: int) -> int:
        response = self.client.read_holding_registers(address, 1, unit=self.unit)
        if response.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return response.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        raw_value = self._read1(address)
        return raw_value / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        raw_value = self._read1(address)
        return raw_value / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        bounded_percent = max(0.0, min(100.0, percent))
        counts = int(round(bounded_percent * 40.95))  # 0-100% -> 0-4095
        response = self.client.write_register(address, counts, unit=self.unit)
        if response.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
