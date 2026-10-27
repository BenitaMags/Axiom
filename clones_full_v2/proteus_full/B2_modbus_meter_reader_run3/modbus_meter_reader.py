"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_client_wrapper(host: str, port: int = 502, unit: int = 1):
    """Bridge pyModbusTCP's ModbusClient to the pymodbus-style interface this
    module was written against.

    pymodbus's ModbusTcpClient exposes `.connect()` to open the socket and
    `.read_holding_registers(address, count, unit=...)`, which returns a
    response object with `.isError()` and `.registers`. pyModbusTCP's
    ModbusClient instead takes the unit id at construction time (`unit_id=`),
    opens the connection via `.open()`, and its
    `.read_holding_registers(address, count)` takes no per-call unit kwarg
    and returns the register list directly, or `None` on failure -- it never
    raises or returns an object with `.isError()`.

    This wrapper constructs a pyModbusTCP `ModbusClient`, aliases
    `.connect()` to `.open()`, and wraps `.read_holding_registers()` so it
    still accepts (and ignores, since the unit id is fixed at construction)
    a per-call `unit` kwarg, and returns a small result object exposing
    `.isError()` (True exactly when the underlying call returned `None`,
    mirroring pymodbus's own error signalling for this same failure mode)
    and `.registers` (the returned list, or `[]` on failure) so callers
    written against pymodbus's response shape keep working unchanged.
    """
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    raw_client = ModbusClient(host=host, port=port, unit_id=unit)

    class _Result:
        def __init__(self, registers):
            self._registers = registers

        def isError(self):
            return self._registers is None

        @property
        def registers(self):
            return self._registers if self._registers is not None else []

    class _ClientAdapter:
        def connect(self):
            return raw_client.open()

        def read_holding_registers(self, address, count, unit=None):
            registers = raw_client.read_holding_registers(address, count)
            return _Result(registers)

    return _ClientAdapter()


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_wrapper(host, port=port)
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
