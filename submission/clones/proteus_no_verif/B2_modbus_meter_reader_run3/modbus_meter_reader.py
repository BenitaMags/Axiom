"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_client_factory(host: str, port: int = 502):
    """Create a Modbus TCP client object usable by the rest of this module.

    pymodbus's ``ModbusTcpClient(host, port=port)`` returns a client object
    that is later connected via ``.connect()``. pyModbusTCP's client class
    (``pyModbusTCP.client.ModbusClient``) instead takes ``host``/``port`` as
    keyword arguments and exposes a different connection/read API. This
    wrapper instantiates ``pyModbusTCP.client.ModbusClient(host=host,
    port=port)`` in place of pymodbus's ``ModbusTcpClient`` so callers can
    keep treating the returned value as "the client" regardless of which
    library backs it.
    """
    from pyModbusTCP.client import ModbusClient

    return ModbusClient(host=host, port=port)


def _axiom_modbus_connect_wrapper(client):
    """Connect a pyModbusTCP client and surface failures like pymodbus does.

    pymodbus's ``client.connect()`` returns a boolean indicating success.
    pyModbusTCP's equivalent method is ``client.open()``, which also returns
    a boolean rather than raising on failure. This wrapper calls
    ``client.open()`` and raises a ``ConnectionError`` if it returns False,
    so call sites written for a "connect-or-fail" flow keep working.
    """
    if not client.open():
        raise ConnectionError(
            f"Unable to connect to Modbus host {getattr(client, 'host', '?')}:"
            f"{getattr(client, 'port', '?')}"
        )
    return client


class _AxiomHoldingRegistersResult:
    """Adapter mimicking a pymodbus read_holding_registers() response.

    pymodbus returns a response object exposing ``.isError()`` and
    ``.registers``. pyModbusTCP's ``client.read_holding_registers()``
    instead returns a plain list of register values on success, or ``None``
    on failure. This class wraps that raw list/``None`` so downstream code
    that expects ``.isError()``/``.registers`` keeps working unchanged.
    """

    def __init__(self, raw_result):
        self._raw_result = raw_result

    def isError(self):
        return self._raw_result is None

    @property
    def registers(self):
        return self._raw_result


def _axiom_read_holding_registers_wrapper(client, address, count):
    """Read holding registers and return a pymodbus-shaped result object.

    pymodbus's ``read_holding_registers(address, count, unit=...)`` returns
    a response object with ``.isError()``/``.registers``. pyModbusTCP's
    ``read_holding_registers(address, count)`` takes no per-call unit id
    (it is pre-set on the client, e.g. via ``client.unit_id = ...``) and
    returns a raw list of register values, or ``None`` on failure. This
    wrapper calls the pyModbusTCP API and wraps the raw list/``None`` into
    an ``_AxiomHoldingRegistersResult`` so call sites keep using
    ``.isError()``/``.registers`` unmodified.
    """
    raw_result = client.read_holding_registers(address, count)
    return _AxiomHoldingRegistersResult(raw_result)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_factory(host, port=port)
    client = _axiom_modbus_connect_wrapper(client)
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        if hasattr(self.client, "unit_id"):
            # pyModbusTCP takes the unit id from the client itself rather than
            # as a per-call keyword argument, so pre-set it here.
            self.client.unit_id = self.unit
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = _axiom_read_holding_registers_wrapper(self.client, address, count)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
