"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusTcpClient  # lazy import for testability

    def _axiom_modbus_client_wrapper(host, port):
        """Bridge pyModbusTCP's client API to the pymodbus-shaped API this module expects.

        pymodbus's ModbusTcpClient exposes `.connect()` and
        `.read_holding_registers(address, count, unit=...)`, where the latter
        returns a response object with `.registers` (a list of ints) and
        `.isError()` (a bool).

        pyModbusTCP's ModbusClient instead takes `host`/`port`/`auto_open` at
        construction time, uses `.open()` instead of `.connect()`, expects the
        unit/slave id to be set via the `.unit_id` attribute rather than a
        `unit=` keyword, and its `read_holding_registers(address, count)`
        returns either a plain list of register values on success or `None`
        on failure (no exception, no response object).

        This wrapper constructs the underlying client (bound to the name
        `ModbusTcpClient` imported above) and returns a small shim object
        that keeps the pymodbus-shaped surface (`.connect()`,
        `.read_holding_registers(address, count, unit=1)` returning something
        with `.registers`/`.isError()`), translating `unit=` into `.unit_id`
        and translating the `list`/`None` result into `.registers`/`.isError()`
        so downstream code does not need to change.
        """

        class _ResultShim:
            def __init__(self, raw_registers):
                self._registers = raw_registers if raw_registers is not None else []
                self._is_error = raw_registers is None

            @property
            def registers(self):
                return self._registers

            def isError(self):
                return self._is_error

        class _ClientShim:
            def __init__(self, host, port):
                self._client = ModbusTcpClient(host=host, port=port, auto_open=True)

            def connect(self):
                return self._client.open()

            def read_holding_registers(self, address, count, unit=1):
                self._client.unit_id = unit
                raw = self._client.read_holding_registers(address, count)
                return _ResultShim(raw)

        return _ClientShim(host, port)

    client = _axiom_modbus_client_wrapper(host, port)
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
