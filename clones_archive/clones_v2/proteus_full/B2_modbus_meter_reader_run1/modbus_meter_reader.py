"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomModbusResult:
    """Adapter result object mimicking pymodbus's response interface.

    pymodbus's read_holding_registers returns a response object exposing
    `.isError()` and `.registers`. pyModbusTCP's read_holding_registers
    instead returns a plain list of register values on success or `None`
    on failure, with no error-checking method. This tiny wrapper class
    re-creates the `.isError()` / `.registers` surface so existing call
    sites do not need to change their error-handling logic.
    """

    def __init__(self, raw):
        self._raw = raw

    def isError(self):
        return self._raw is None

    @property
    def registers(self):
        return self._raw if self._raw is not None else []


class _AxiomModbusClientAdapter:
    """Adapter exposing a pymodbus-like client interface backed by pyModbusTCP.

    pymodbus's `ModbusTcpClient` exposes `.connect()` and
    `.read_holding_registers(address, count, unit=...)` returning a response
    object with `.isError()`/`.registers`. pyModbusTCP's `ModbusClient` uses
    `.open()` instead of `.connect()`, has no concept of a Modbus `unit`/slave
    id for plain TCP, and `.read_holding_registers(address, count)` returns a
    raw list (or `None` on failure). This adapter translates between the two
    so existing call sites written against the pymodbus-style API keep
    working unmodified.
    """

    def __init__(self, modbus_client):
        self._modbus_client = modbus_client

    def connect(self):
        return self._modbus_client.open()

    def read_holding_registers(self, address, count, unit=None):
        # `unit` is accepted for API compatibility but dropped: pyModbusTCP's
        # ModbusClient does not support a per-call unit/slave id for TCP.
        raw = self._modbus_client.read_holding_registers(address, count)
        return _AxiomModbusResult(raw)


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Construct and connect a pyModbusTCP-backed client with a pymodbus-like API.

    pymodbus's `ModbusTcpClient(host, port=port)` followed by `.connect()`
    returns a client whose `.read_holding_registers(...)` yields a response
    object with `.isError()`/`.registers`. pyModbusTCP's `ModbusClient`
    constructor takes `host=`/`port=` keyword arguments and uses `.open()`
    rather than `.connect()`, and its `read_holding_registers` returns a raw
    list or `None`. This wrapper builds the pyModbusTCP client, opens the
    connection, and returns an `_AxiomModbusClientAdapter` so the rest of
    this module can keep using the pymodbus-style `.connect()` /
    `.read_holding_registers(address, count, unit=...)` / `.isError()` /
    `.registers` calling convention.
    """
    from pyModbusTCP.client import ModbusClient

    modbus_client = ModbusClient(host=host, port=port)
    modbus_client.open()
    return _AxiomModbusClientAdapter(modbus_client)


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
