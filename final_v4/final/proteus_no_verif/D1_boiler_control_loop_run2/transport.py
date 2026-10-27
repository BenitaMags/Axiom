"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from modbus_adapter import build_tcp_client


def _default_client(host: str, port: int = 502):
    client = build_tcp_client(host, port)
    client.connect()
    return client


class _RegisterCodec:
    """Encapsulates a register's scale factor and clamp/round behaviour."""

    def __init__(self, scale: float, clamp_lo: float = None, clamp_hi: float = None):
        self.scale = scale
        self.clamp_lo = clamp_lo
        self.clamp_hi = clamp_hi

    def decode(self, raw: int) -> float:
        return raw / self.scale

    def encode(self, value: float) -> int:
        v = value
        if self.clamp_lo is not None:
            v = max(self.clamp_lo, v)
        if self.clamp_hi is not None:
            v = min(self.clamp_hi, v)
        return int(round(v * self.scale))


_TEMPERATURE_CODEC = _RegisterCodec(scale=10.0)          # deci-degrees C -> C
_PRESSURE_CODEC = _RegisterCodec(scale=100.0)            # centibar -> bar
_VALVE_CODEC = _RegisterCodec(scale=40.95, clamp_lo=0.0, clamp_hi=100.0)  # 0-100% -> 0-4095


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def _read_scaled(self, address: int, codec: _RegisterCodec) -> float:
        return codec.decode(self._read1(address))

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read_scaled(address, _TEMPERATURE_CODEC)

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read_scaled(address, _PRESSURE_CODEC)

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = _VALVE_CODEC.encode(percent)
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
