"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.

The low-level bridge that adapts modbus-tk's TcpMaster to a pymodbus-shaped API
lives in `modbus_bridge.py`; this module focuses on the engineering-unit
conversions and the small state machine around a single Modbus client.
"""

from dataclasses import dataclass

from modbus_bridge import (
    _AxiomModbusResponse,
    _axiom_modbus_client_wrapper,
    _default_client,
)


def _ensure_ok(response, address: int, verb: str) -> None:
    """Raise IOError in the same shape the original inline checks used."""
    if response.isError():
        raise IOError(f"Modbus {verb} failed @ {address:#06x}")


@dataclass(frozen=True)
class _ScaledRegister:
    """A holding register whose raw integer value is divided by `scale`."""

    scale: float

    def to_engineering(self, raw: int) -> float:
        return raw / self.scale


# Table of the (previously duplicated) scaling constants used by the two
# read helpers. Adding a new scaled quantity means adding a row here, not a
# new near-identical method.
_READ_SPECS = {
    "temperature": _ScaledRegister(scale=10.0),   # deci-degrees C -> C
    "pressure": _ScaledRegister(scale=100.0),     # centibar -> bar
}


@dataclass(frozen=True)
class _ValveCommand:
    """Encapsulates the valve percent -> raw-count conversion rule."""

    percent: float
    min_percent: float = 0.0
    max_percent: float = 100.0
    counts_per_percent: float = 40.95  # 0-100% -> 0-4095

    def counts(self) -> int:
        clamped = max(self.min_percent, min(self.max_percent, self.percent))
        return int(round(clamped * self.counts_per_percent))


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        _ensure_ok(rr, address, "read")
        return rr.registers[0]

    def _read_scaled(self, address: int, spec_key: str) -> float:
        spec = _READ_SPECS[spec_key]
        return spec.to_engineering(self._read1(address))

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read_scaled(address, "temperature")

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read_scaled(address, "pressure")

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = _ValveCommand(percent).counts()
        wr = self.client.write_register(address, counts, unit=self.unit)
        _ensure_ok(wr, address, "write")
        return counts
