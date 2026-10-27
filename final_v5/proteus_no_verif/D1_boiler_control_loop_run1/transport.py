"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from _modbustk_adapter import _modbustk_client_adapter, _ModbusTkClientAdapter, _ModbusTkResponse


def _default_client(host: str, port: int = 502):
    client = _modbustk_client_adapter(host, port=port)
    client.connect()
    return client


# Table of (register address, scale factor) for the simple scaled reads.
# Value read is divided by the scale factor to get engineering units.
_READ_SCALES = {
    "temperature": 10.0,   # deci-degrees C -> C
    "pressure": 100.0,     # centibar -> bar
}


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def _read_scaled(self, address: int, scale_key: str) -> float:
        return self._read1(address) / _READ_SCALES[scale_key]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read_scaled(address, "temperature")

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read_scaled(address, "pressure")

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        clamped = max(0.0, min(100.0, percent))
        counts = int(round(clamped * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
