"""transport -- Modbus field I/O for the boiler supervisor.

Clone of the reference transport.  The wire-level facts (register addresses, scale
factors, unit id, error text) are unchanged; the organisation is a register map of
``Channel`` descriptors plus one generic fetch/store pair, rather than a read helper
and three hand-written methods that each repeat their address and scale factor.
"""
from typing import NamedTuple

from saturation import VALVE_TRAVEL


class Channel(NamedTuple):
    """One holding register and the number of raw counts per engineering unit."""
    address: int
    counts_per_unit: float

    def to_engineering(self, raw: int) -> float:
        return raw / self.counts_per_unit

    def to_counts(self, value: float) -> int:
        return round(value * self.counts_per_unit)


TEMPERATURE = Channel(0x0000, 10.0)   # register holds deci-degrees C
PRESSURE = Channel(0x0001, 100.0)     # register holds centibar
VALVE = Channel(0x0010, 40.95)        # 0-100 % spans 0-4095 counts


def open_tcp(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy: the tests inject a fake instead

    client = ModbusTcpClient(host, port=port)
    client.connect()
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.unit = unit
        self.client = client
        if client is None:
            self.client = open_tcp(host)

    # ---- generic register access ----
    @staticmethod
    def _require_ok(reply, verb: str, address: int):
        if reply.isError():
            raise IOError(f"Modbus {verb} failed @ {address:#06x}")

    def _fetch(self, channel: Channel) -> float:
        reply = self.client.read_holding_registers(channel.address, 1, unit=self.unit)
        self._require_ok(reply, "read", channel.address)
        return channel.to_engineering(reply.registers[0])

    def _store(self, channel: Channel, value: float) -> int:
        counts = channel.to_counts(value)
        reply = self.client.write_register(channel.address, counts, unit=self.unit)
        self._require_ok(reply, "write", channel.address)
        return counts

    # ---- named process variables ----
    def read_temperature(self, address: int = TEMPERATURE.address) -> float:
        return self._fetch(TEMPERATURE._replace(address=address))

    def read_pressure(self, address: int = PRESSURE.address) -> float:
        return self._fetch(PRESSURE._replace(address=address))

    def write_valve(self, percent: float, address: int = VALVE.address) -> int:
        return self._store(VALVE._replace(address=address), VALVE_TRAVEL.pin(percent))
