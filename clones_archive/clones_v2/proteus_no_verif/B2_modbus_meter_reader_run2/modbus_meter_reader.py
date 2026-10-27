"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_client_adapter(host: str, port: int = 502, unit: int = 1):
    """Adapt pyModbusTCP.client.ModbusClient to the pymodbus client interface.

    pymodbus's ModbusTcpClient exposes read_holding_registers(address, count,
    unit=...) returning a response object with `.registers` (a list of ints)
    and `.isError()` (a bool). pyModbusTCP's ModbusClient instead exposes
    read_holding_registers(address, count) with no per-call `unit` argument
    -- the unit/slave id is fixed on the client (via `unit_id=...` at
    construction, or the `unit_id()` getter/setter method) -- and it returns
    either a plain list of register values on success, or `None` on failure.

    This wrapper builds a pyModbusTCP ModbusClient bound to `host`/`port`/
    `unit`, adds a `connect()` method that maps onto pyModbusTCP's `.open()`,
    and wraps `read_holding_registers` so existing call sites written against
    the pymodbus API keep working unchanged: if a per-call `unit` differs
    from the client's currently configured unit id, the client's unit id is
    updated before issuing the read; the resulting raw list/`None` is then
    repackaged into a small result object exposing `.registers` (empty list
    on failure) and `.isError()` (True precisely when pyModbusTCP returned
    `None`), preserving the existing error-handling semantics.
    """
    from pyModbusTCP.client import ModbusClient

    class _Result:
        def __init__(self, registers):
            self.registers = registers if registers is not None else []
            self._is_error = registers is None

        def isError(self):
            return self._is_error

    class _ClientAdapter:
        def __init__(self, host, port, unit):
            self._client = ModbusClient(host=host, port=port, unit_id=unit)

        def connect(self):
            return self._client.open()

        def read_holding_registers(self, address, count, unit=None):
            if unit is not None and unit != self._client.unit_id():
                self._client.unit_id(unit)
            registers = self._client.read_holding_registers(address, count)
            return _Result(registers)

    return _ClientAdapter(host, port, unit)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_adapter(host, port)
    client.connect()
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
