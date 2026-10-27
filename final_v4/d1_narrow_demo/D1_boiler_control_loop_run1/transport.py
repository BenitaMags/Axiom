"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.

The actual pymodbus-to-modbus_tk bridging machinery lives in
`modbustk_bridge`, a self-contained helper module. This module concerns
itself only with the boiler-specific register map and unit conversions.
"""

from typing import NamedTuple

from modbustk_bridge import build_default_client


class RegisterSpec(NamedTuple):
    """A single scaled holding-register reading: where it lives, how to scale it."""

    address: int
    scale: float

    def to_units(self, raw: int) -> float:
        return raw / self.scale


class ValveOutput(NamedTuple):
    """The valve's output register and its percent -> counts mapping."""

    address: int
    counts_per_percent: float
    minimum_percent: float = 0.0
    maximum_percent: float = 100.0

    def to_counts(self, percent: float) -> int:
        clamped = max(self.minimum_percent, min(self.maximum_percent, percent))
        return int(round(clamped * self.counts_per_percent))


class ModbusIO:
    # Register map, expressed as data rather than scattered magic numbers.
    TEMPERATURE = RegisterSpec(address=0x0000, scale=10.0)   # deci-degrees C -> C
    PRESSURE = RegisterSpec(address=0x0001, scale=100.0)     # centibar -> bar
    VALVE = ValveOutput(address=0x0010, counts_per_percent=40.95)  # 0-100% -> 0-4095

    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else build_default_client(host)
        self.unit = unit

    def _read_register(self, address: int) -> int:
        response = self.client.read_holding_registers(address, 1, unit=self.unit)
        if response.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return response.registers[0]

    def _read_scaled(self, address: int, spec: RegisterSpec) -> float:
        return spec.to_units(self._read_register(address))

    def read_temperature(self, address: int = TEMPERATURE.address) -> float:
        return self._read_scaled(address, self.TEMPERATURE)

    def read_pressure(self, address: int = PRESSURE.address) -> float:
        return self._read_scaled(address, self.PRESSURE)

    def write_valve(self, percent: float, address: int = VALVE.address) -> int:
        counts = self.VALVE.to_counts(percent)
        response = self.client.write_register(address, counts, unit=self.unit)
        if response.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
