"""transport -- Modbus field I/O for the boiler supervisor.

Uses `pyModbusTCP` instead of `pymodbus` for the underlying Modbus TCP
transport. pyModbusTCP's client (`pyModbusTCP.client.ModbusClient`) has a
different API shape than pymodbus's: reads return either a plain list of
register values or `None` on failure (rather than an object with `.isError()`
and `.registers`), and writes return `True`/`False`/`None` instead of a
response object. This module wraps that client in a small adapter that
reproduces the read/write API and error model (raising `IOError`) the rest
of the program relies on. The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True, auto_close=False)
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: int) -> int:
        if hasattr(self.client, "unit_id"):
            self.client.unit_id = self.unit
        regs = self.client.read_holding_registers(address, 1)
        if regs is None:
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return regs[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        if hasattr(self.client, "unit_id"):
            self.client.unit_id = self.unit
        ok = self.client.write_single_register(address, counts)
        if not ok:
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
