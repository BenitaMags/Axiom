# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.

Migrated from pymodbus to pyModbusTCP. pyModbusTCP's client exposes
`read_holding_registers(reg_addr, reg_nb)` which returns either a plain list
of register ints on success or `None` on failure -- a different shape than
pymodbus's response object (which had `.isError()` and `.registers`). This
module adapts that functional-list-or-None API back into an object exposing
`.isError()` and `.registers`, so the rest of the module (and its unit tests,
which are written against that shape) keeps working unchanged.

The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _Result:
    """Adapts pyModbusTCP's list-or-None return value to look like the
    old pymodbus response object this module was written against.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _PyModbusTcpClientAdapter:
    """Adapts pyModbusTCP's ModbusClient to the read_holding_registers(
    address, count, unit=...) shape this module was originally written
    against for pymodbus.
    """

    def __init__(self, host: str, port: int = 502, unit: int = 1):
        from pyModbusTCP.client import ModbusClient

        self._client = ModbusClient(host=host, port=port, unit_id=unit, auto_open=True)

    def read_holding_registers(self, address, count, unit=1):
        # pyModbusTCP addresses the unit id on the client itself, not per-call.
        if self._client.unit_id != unit:
            self._client.unit_id = unit
        regs = self._client.read_holding_registers(address, count)
        if regs is None:
            return _Result(error=True)
        return _Result(registers=list(regs), error=False)

    def close(self):
        self._client.close()


def _default_client(host: str, port: int = 502):
    client = _PyModbusTcpClientAdapter(host, port=port)
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self.client.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
