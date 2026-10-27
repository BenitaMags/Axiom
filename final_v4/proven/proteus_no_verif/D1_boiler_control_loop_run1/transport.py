"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from axiom_modbus_adapter import (
    AxiomModbusResponse as _AxiomModbusResponse,
    AxiomModbusClientAdapter as _AxiomModbusClientAdapter,
    axiom_modbus_client_wrapper as _axiom_modbus_client_wrapper,
)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_client_wrapper(host, port)
    client.connect()
    return client


class ModbusIO:

    _SCALES = {
        "temperature": 10.0,
        "pressure": 100.0,
    }

    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def _read_scaled(self, address: int, scale_key: str) -> float:
        return self._read1(address) / self._SCALES[scale_key]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read_scaled(address, "temperature")  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read_scaled(address, "pressure")  # centibar -> bar

    @staticmethod
    def _percent_to_counts(percent: float) -> int:
        clamped = max(0.0, min(100.0, percent))
        return int(round(clamped * 40.95))  # 0-100% -> 0-4095

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = self._percent_to_counts(percent)
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
