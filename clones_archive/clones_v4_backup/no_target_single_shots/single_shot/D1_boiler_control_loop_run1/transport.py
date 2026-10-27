# CHOSEN-TARGET: pyModbusTCP
"""transport -- Modbus field I/O for the boiler supervisor.


read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True)
    client.open()
    return client


class _ReadResult:
    """Small adapter mimicking the pymodbus read-response shape."""

    __slots__ = ("registers", "_error")

    def __init__(self, registers):
        self.registers = registers
        self._error = registers is None

    def isError(self) -> bool:
        return self._error


class _WriteResult:
    """Small adapter mimicking the pymodbus write-response shape."""

    __slots__ = ("_ok",)

    def __init__(self, ok):
        self._ok = ok

    def isError(self) -> bool:
        return not self._ok


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _perform_read(self, address: int) -> _ReadResult:
        # Prefer a pyModbusTCP-style unit-id setter if the injected client has one;
        # fall back gracefully for stub/mock clients used in tests.
        setter = getattr(self.client, "unit_id", None)
        if callable(setter):
            try:
                self.client.unit_id(self.unit)
            except TypeError:
                pass

        raw = self.client.read_holding_registers(address, 1, unit=self.unit)
        # Support both "raw list" style clients and pymodbus-like response objects.
        if raw is None:
            return _ReadResult(None)
        if hasattr(raw, "isError"):
            return raw
        return _ReadResult(list(raw))

    def _read1(self, address: str) -> int:
        rr = self._perform_read(address)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def _perform_write(self, address: int, value: int) -> _WriteResult:
        result = self.client.write_register(address, value, unit=self.unit)
        if hasattr(result, "isError"):
            return result
        return _WriteResult(bool(result))

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        bounded_pct = max(0.0, min(100.0, percent))
        counts = int(round(bounded_pct * 40.95))  # 0-100% -> 0-4095
        wr = self._perform_write(address, counts)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
