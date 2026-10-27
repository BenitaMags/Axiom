# CHOSEN-TARGET: pyModbusTCP
"""transport -- Modbus field I/O for the boiler supervisor.


read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True)
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        # Adapt to pyModbusTCP's read_holding_registers(address, number, unit_id=...)
        # which returns a list of registers on success or None on error.
        rr = self.client.read_holding_registers(address, 1, unit_id=self.unit)
        if rr is None:
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        # Adapt to pyModbusTCP's write_single_register(address, value, unit_id=...)
        # which returns True on success or False on error.
        wr = self.client.write_single_register(address, counts, unit_id=self.unit)
        if not wr:
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
