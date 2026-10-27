"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_client_factory(host: str, port: int = 502, unit: int = 1):
    """Create a pyModbusTCP client in place of pymodbus's ModbusTcpClient.

    pymodbus's ModbusTcpClient(host, port=port) is a constructor that only
    configures the client; connecting happens via a later, separate
    .connect() call. pyModbusTCP's ModbusClient constructor accepts host,
    port, and unit_id directly, and also defers the actual TCP connection.
    This wrapper translates the pymodbus-style constructor arguments into
    the pyModbusTCP equivalent and returns the configured (not yet
    connected) client.
    """
    from pyModbusTCP.client import ModbusClient

    return ModbusClient(host=host, port=port, unit_id=unit)


def _axiom_modbus_connect_wrapper(client):
    """Open the pyModbusTCP connection in place of pymodbus's client.connect().

    pymodbus's client.connect() opens the TCP socket and returns True/False
    for success. pyModbusTCP uses .open() instead of .connect(), also
    returning True/False, and exposes .is_open to check current state.
    This wrapper calls .open() and raises ConnectionError if the socket
    did not come up, so failures are surfaced rather than silently
    producing an unusable client.
    """
    client.open()
    if not client.is_open:
        raise ConnectionError("Unable to connect to Modbus host")
    return True


class _AxiomModbusResult:
    """Adapt pyModbusTCP's raw register list/None into pymodbus's response interface.

    pymodbus's read_holding_registers returns a response object exposing
    .isError() and .registers. pyModbusTCP's read_holding_registers returns
    a plain list of register values on success, or None on failure -- there
    is no response object and no exception on a failed read. This adapter
    wraps the raw pyModbusTCP result so that .isError() is True exactly
    when the raw result is None, and .registers exposes the raw list (or an
    empty list on error), letting existing pymodbus-shaped call sites keep
    working unchanged.
    """

    def __init__(self, raw):
        self._raw = raw

    def isError(self):
        return self._raw is None

    @property
    def registers(self):
        return self._raw if self._raw is not None else []


def _axiom_modbus_result_wrapper(raw):
    """Normalize a Modbus read result to a pymodbus-shaped response object.

    Some injected test/fake clients already return a pymodbus-shaped
    response object exposing both .isError() and .registers (mirroring the
    original pymodbus contract). pyModbusTCP's real read_holding_registers,
    however, returns a plain list of register values on success or None on
    failure -- there is no response object and no exception on a failed
    read. This wrapper detects which shape it was handed: if the raw result
    already looks pymodbus-shaped (has both isError and registers), it is
    passed through unchanged; otherwise it is treated as a pyModbusTCP raw
    list/None and wrapped in _AxiomModbusResult so callers can keep using
    .isError()/.registers regardless of which client implementation is in
    play.
    """
    if hasattr(raw, "isError") and hasattr(raw, "registers"):
        return raw
    return _AxiomModbusResult(raw)


def _axiom_read_holding_registers_wrapper(client, address, count, unit):
    """Read holding registers via pyModbusTCP using pymodbus's call shape.

    pymodbus's read_holding_registers(address, count, unit=...) accepts the
    target unit/slave id as a keyword on every call. pyModbusTCP's
    read_holding_registers(address, count) takes no unit kwarg -- the unit
    id must be set on the client beforehand via its unit_id attribute. This
    wrapper sets client.unit_id when supported, issues the read, and passes
    the result through _axiom_modbus_result_wrapper, which normalizes
    either a raw pyModbusTCP list/None or an already pymodbus-shaped
    response (as used by some test doubles) into a consistent
    .isError()/.registers interface.
    """
    if hasattr(client, "unit_id"):
        client.unit_id = unit
        raw = client.read_holding_registers(address, count)
    else:
        raw = client.read_holding_registers(address, count, unit=unit)
    return _axiom_modbus_result_wrapper(raw)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_client_factory(host, port=port)
    _axiom_modbus_connect_wrapper(client)
    return client


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
