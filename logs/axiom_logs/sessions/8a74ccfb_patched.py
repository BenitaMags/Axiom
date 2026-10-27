"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomModbusTkResult:
    """Shim result object mimicking pymodbus's response object.

    pymodbus's ``read_holding_registers`` returns a response object exposing
    ``.registers`` (a list of ints) and ``.isError()`` (a bool). modbus-tk's
    ``master.execute(...)`` instead returns a bare tuple of register values on
    success, and raises a ``modbus_tk.exceptions.ModbusError`` (or similar) on
    failure. This shim wraps either outcome into an object with the same
    ``.registers``/``.isError()`` shape pymodbus callers expect, so
    ``MeterReader.read_measurement`` needs no changes.
    """

    def __init__(self, registers=None, error=False):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _AxiomModbusTkClientWrapper:
    """Adapter exposing a pymodbus-ModbusTcpClient-like interface over modbus-tk.

    pymodbus's ``ModbusTcpClient`` is constructed with ``(host, port=port)``,
    then ``.connect()``/``.close()`` manage the socket, and
    ``.read_holding_registers(address, count, unit=...)`` returns a response
    object with ``.registers``/``.isError()``.

    modbus-tk instead uses a ``TcpMaster(host, port)`` object whose ``.open()``
    /``.close()`` manage the socket, and reads happen via
    ``master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)``, which
    returns a plain tuple of register values on success and raises a modbus-tk
    exception on failure.

    This wrapper builds and opens a ``TcpMaster`` on construction, aliases
    ``connect()``/``close()`` to ``open()``/``close()`` so existing lifecycle
    calls keep working, and translates ``execute()``'s tuple-or-raise behavior
    into a ``_AxiomModbusTkResult`` so callers can keep using
    ``.registers``/``.isError()`` exactly as they did with pymodbus.
    """

    def __init__(self, host: str, port: int = 502):
        import modbus_tk.modbus_tcp as modbus_tcp

        self._master = modbus_tcp.TcpMaster(host=host, port=port)
        self._master.set_timeout(5.0)
        self._master.open()

    def connect(self):
        self._master.open()
        return True

    def close(self):
        self._master.close()

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst
        import modbus_tk.exceptions as mtk_exceptions

        try:
            values = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except mtk_exceptions.ModbusError:
            return _AxiomModbusTkResult(error=True)
        return _AxiomModbusTkResult(registers=values, error=False)


def _axiom_modbus_tk_client_wrapper(host: str, port: int = 502):
    """Build a modbus-tk-backed client shim matching pymodbus's ModbusTcpClient.

    pymodbus's ``ModbusTcpClient(host, port=port)`` constructs a client that
    must then be explicitly ``.connect()``-ed. modbus-tk's ``TcpMaster``
    opens its socket immediately upon construction, and reads are performed
    through ``master.execute(...)`` rather than a
    ``read_holding_registers(...)`` method returning a response object.

    This function returns an ``_AxiomModbusTkClientWrapper`` instance, which
    opens the modbus-tk connection eagerly (so a subsequent ``.connect()``
    call is a harmless no-op alias) and exposes ``read_holding_registers``
    with the pymodbus-compatible return shape, so the rest of this module
    does not need to know which underlying library is in use.
    """
    return _AxiomModbusTkClientWrapper(host, port=port)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_tk_client_wrapper(host, port=port)
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
