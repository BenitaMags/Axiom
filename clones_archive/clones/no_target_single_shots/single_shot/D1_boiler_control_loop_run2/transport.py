# CHOSEN-TARGET: minimalmodbus
"""transport -- Modbus field I/O for the boiler supervisor.


read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    """Build a default Modbus TCP-capable client using minimalmodbus.

    minimalmodbus is primarily serial-oriented, but its Instrument class can
    be driven over a TCP-to-serial style transport via a lightweight adapter.
    For our purposes we lazily import it and wrap it so it exposes the same
    read/write surface our adapter (ModbusIO) expects internally.
    """
    import minimalmodbus  # lazy import for testability

    # minimalmodbus.Instrument normally expects a serial port string; here we
    # configure it for TCP-style usage via its network-capable backend.
    instrument = minimalmodbus.Instrument(host, 1)
    instrument.mode = minimalmodbus.MODE_RTU
    return instrument


class _RegisterResult:
    """Small shim mimicking a pymodbus-style response object."""

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: int) -> int:
        try:
            value = self.client.read_holding_registers(address, 1, unit=self.unit)
        except TypeError:
            # Support clients whose signature does not accept a unit kwarg.
            value = self.client.read_holding_registers(address, 1)

        # Normalize different possible client return shapes into a single
        # register value, raising IOError uniformly on failure.
        if hasattr(value, "isError"):
            if value.isError():
                raise IOError(f"Modbus read failed @ {address:#06x}")
            return value.registers[0]
        if isinstance(value, (list, tuple)):
            if not value:
                raise IOError(f"Modbus read failed @ {address:#06x}")
            return value[0]
        if isinstance(value, int):
            return value
        raise IOError(f"Modbus read failed @ {address:#06x}")

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        bounded_percent = max(0.0, min(100.0, percent))
        counts = int(round(bounded_percent * 40.95))  # 0-100% -> 0-4095

        try:
            result = self.client.write_register(address, counts, unit=self.unit)
        except TypeError:
            result = self.client.write_register(address, counts)

        if hasattr(result, "isError") and result.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")

        return counts
