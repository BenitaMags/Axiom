# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True)
    return client


class _ResultWrapper:
    """Adapter that mimics the pymodbus response object's interface
    (``.isError()`` and ``.registers``) on top of pyModbusTCP's plain
    list-or-None return value.
    """

    def __init__(self, registers):
        self.registers = registers if registers is not None else []

    def isError(self) -> bool:
        return self.registers is None or len(self.registers) == 0


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_holding_registers(self, address, count, unit=1):
        """Wrap the underlying client's read call, adapting the result to an
        object exposing ``.isError()`` and ``.registers`` like pymodbus does.
        """
        if hasattr(self.client, "read_holding_registers"):
            try:
                registers = self.client.read_holding_registers(address, count)
            except TypeError:
                # some clients (e.g. test doubles) may accept a unit kwarg
                registers = self.client.read_holding_registers(
                    address, count, unit=unit
                )
        else:
            registers = None

        if isinstance(registers, list) or registers is None:
            return _ResultWrapper(registers)
        # Already a response-like object (e.g. a test double)
        return registers

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
