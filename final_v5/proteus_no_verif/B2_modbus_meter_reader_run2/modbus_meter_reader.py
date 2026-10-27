"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient construction/connect convention to pyModbusTCP.

    pymodbus: ``ModbusTcpClient(host, port=port)`` builds a client object that
    is later connected via a ``.connect()`` method returning a bool.

    pyModbusTCP: ``ModbusClient(host=host, port=port)`` builds an equivalent
    client, but the socket is opened via ``.open()`` instead of ``.connect()``;
    there is no ``.connect()`` method at all.

    This wrapper constructs the pyModbusTCP client and attaches a ``.connect``
    attribute that forwards to ``.open()``, so existing call sites written
    against pymodbus's ``.connect()`` naming keep working unmodified.
    """
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port)
    client.connect = client.open
    return client


class _HoldingRegistersResult:
    """Wrap pyModbusTCP's raw register read result to look like pymodbus's response object.

    pymodbus's ``read_holding_registers`` returns a response object exposing
    ``.isError()`` and ``.registers``.

    pyModbusTCP's ``read_holding_registers`` instead returns a plain Python
    list of register values on success, or ``None`` on failure -- there is no
    response object and no exception raised for a failed read.

    This class wraps that raw value: ``.registers`` exposes the list (empty
    if the read failed) and ``.isError()`` reports True exactly when the raw
    result was ``None``, so callers written against pymodbus's API observe
    the same behavior.
    """

    def __init__(self, raw):
        self._raw = raw
        if not (raw is not None):
            self.registers = []
        else:
            self.registers = raw

    def isError(self):
        _spt_r1 = self._raw is None
        return _spt_r1


def _axiom_read_holding_registers_wrapper(client, address, count, unit):
    """Bridge pymodbus's per-call unit id to pyModbusTCP's client-attribute unit id.

    pymodbus: ``client.read_holding_registers(address, count, unit=unit)``
    passes the slave/unit id as a keyword argument on every call, and returns
    a response object with ``.isError()``/``.registers``.

    pyModbusTCP: the unit id is not a call argument. It must be set as the
    ``unit_id`` attribute on the client beforehand, and
    ``read_holding_registers(address, count)`` takes only the address and
    count, returning a raw list or ``None``.

    This wrapper sets ``client.unit_id = unit``, performs the read with the
    two positional arguments pyModbusTCP expects, and wraps the raw result in
    ``_HoldingRegistersResult`` so downstream code can keep calling
    ``.isError()`` and ``.registers`` unchanged.
    """
    client.unit_id = unit
    raw = client.read_holding_registers(address, count)
    _spt_r2 = _HoldingRegistersResult(raw)
    return _spt_r2


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_client_wrapper(host, port=port)
    client.connect()
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if not (client is not None):
            self.client = _default_client(host)
        else:
            self.client = client
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = _axiom_read_holding_registers_wrapper(self.client, address, count, self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
