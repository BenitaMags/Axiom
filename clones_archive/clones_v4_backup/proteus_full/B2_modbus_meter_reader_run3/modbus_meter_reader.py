"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_client_wrapper(host: str, port: int = 502, unit: int = 1):
    """Bridge pymodbus's client/response API to pyModbusTCP's ModbusClient.

    pymodbus's `ModbusTcpClient` exposes `connect()`/`close()` lifecycle methods
    and a `read_holding_registers(address, count, unit=...)` call that returns a
    response object with `.isError()` and `.registers`.

    pyModbusTCP's `ModbusClient` instead exposes `open()`/`close()` and a
    `read_holding_registers(reg_addr, reg_nb)` call that returns a plain list of
    register values on success, or `None` on failure -- there is no `.isError()`
    flag and no `.registers` attribute.

    This wrapper constructs a pyModbusTCP `ModbusClient` (with `auto_open=True`
    so reads implicitly (re)open the connection) and returns a small adapter
    object that keeps the pymodbus-shaped surface (`connect()`, `close()`, and
    `read_holding_registers(address, count, unit=...)` returning an object with
    `.isError()`/`.registers`) so call sites written against pymodbus's API do
    not need to change their control flow.
    """
    from pyModbusTCP.client import ModbusClient

    class _Result:
        def __init__(self, registers):
            self._registers = registers

        def isError(self):
            return self._registers is None

        @property
        def registers(self):
            return self._registers if self._registers is not None else []

    class _Adapter:
        def __init__(self, host, port, unit):
            self._client = ModbusClient(
                host=host, port=port, unit_id=unit, auto_open=True
            )

        def connect(self):
            return self._client.open()

        def close(self):
            return self._client.close()

        def read_holding_registers(self, address, count, unit=None):
            raw = self._client.read_holding_registers(reg_addr=address, reg_nb=count)
            return _Result(raw)

    return _Adapter(host, port, unit)


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
