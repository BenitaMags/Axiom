"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomModbusResponse:
    """Adapter that mimics pymodbus's response object.

    pymodbus's read_holding_registers returns an object exposing `.isError()`
    and `.registers`. pyModbusTCP's read_holding_registers instead returns a
    raw list of register values on success, or `None` on failure (there is no
    response object and no `.isError()` method). This lightweight adapter
    wraps that raw value so downstream code can keep calling `.isError()` /
    `.registers` exactly as it did against pymodbus.
    """

    def __init__(self, raw):
        self._raw = raw

    def isError(self):
        return self._raw is None

    @property
    def registers(self):
        return self._raw if self._raw is not None else []


def _axiom_modbus_client_factory(host: str, port: int = 502, unit: int = 1):
    """Construct a pyModbusTCP client that mimics the pymodbus client surface.

    pymodbus builds a client via `ModbusTcpClient(host, port=port)` and opens
    the connection with `.connect()`. pyModbusTCP's `ModbusClient` instead
    takes `host=host, port=port, unit_id=<slave id>, auto_open=True` at
    construction time and opens the socket via `.open()` (or automatically,
    thanks to `auto_open=True`). This wrapper builds the pyModbusTCP client,
    ensures the connection is open, and attaches a `.connect()` alias to
    `.open()` so existing call sites that expect a pymodbus-style `.connect()`
    method keep working unchanged.
    """
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, unit_id=unit, auto_open=True)
    client.open()
    client.connect = client.open
    return client


def _axiom_read_holding_registers_wrapper(client, address, count, unit):
    """Read holding registers via pyModbusTCP and adapt the result to pymodbus's shape.

    pymodbus reads registers via `read_holding_registers(address, count,
    unit=unit)`, returning a response object with `.isError()` / `.registers`.
    pyModbusTCP instead tracks the unit/slave id as the `.unit_id` attribute
    on the client (not a call kwarg), and its `read_holding_registers(address,
    count)` returns a raw list of registers on success or `None` on failure.
    This wrapper sets `client.unit_id` before the call, invokes the
    pyModbusTCP-style read, and wraps the raw result in
    `_AxiomModbusResponse` so callers can keep using `.isError()` /
    `.registers` as before.
    """
    client.unit_id = unit
    raw = client.read_holding_registers(address, count)
    return _AxiomModbusResponse(raw)


def _default_client(host: str, port: int = 502):

    return _axiom_modbus_client_factory(host, port=port)


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = _axiom_read_holding_registers_wrapper(
                self.client, address, count, self.unit
            )
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
