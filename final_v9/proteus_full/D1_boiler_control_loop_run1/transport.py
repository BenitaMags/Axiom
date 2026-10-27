"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.

The pyModbusTCP adapter machinery and the read/write result normalization live in
their own helper modules (`modbus_adapter` and `modbus_result`); this module wires
them together into the public `ModbusIO` field-I/O facade.
"""

from typing import NamedTuple

from modbus_adapter import (
    _AxiomModbusClientAdapter,
    _axiom_modbus_client_factory,
    _axiom_read_holding_registers_wrapper,
    _axiom_write_register_wrapper,
)
from modbus_result import (
    _axiom_normalize_read_result_wrapper,
    _axiom_normalize_write_result_wrapper,
)


class _ScaledRegister(NamedTuple):
    """A holding-register address paired with the divisor used to convert
    its raw integer count into the engineering unit callers expect (e.g.
    deci-degrees C -> C, centibar -> bar)."""

    address: int
    divisor: float


def _default_client(host: str, port: int = 502):
    return _axiom_modbus_client_factory(host, port)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        return _axiom_normalize_read_result_wrapper(
            self.client.read_holding_registers(address, self.unit)
        )

    def _read_scaled(self, register: _ScaledRegister) -> float:
        return self._read1(register.address) / register.divisor

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read_scaled(_ScaledRegister(address, 10.0))  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read_scaled(_ScaledRegister(address, 100.0))  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        return _axiom_normalize_write_result_wrapper(
            self.client.write_register(address, counts, self.unit), counts
        )
