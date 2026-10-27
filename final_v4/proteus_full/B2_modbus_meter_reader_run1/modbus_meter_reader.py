"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomModbusResult:
    """Shim result object mimicking pymodbus's response interface.

    pymodbus's ``read_holding_registers`` returns a response object with an
    ``.isError()`` method and a ``.registers`` list attribute. modbus-tk's
    ``TcpMaster.execute`` instead returns a plain tuple of register values on
    success, or raises a ``modbus_tk.exceptions.ModbusError`` (or subclass) on
    failure. This shim normalizes both cases back into the pymodbus-shaped
    object so that ``read_measurement`` below does not need to change its
    error-handling logic.
    """

    def __init__(self, registers=None, error=False):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _AxiomTcpMasterClient:
    """Shim client exposing a pymodbus-like ``read_holding_registers`` method
    backed by a modbus_tk ``TcpMaster``.

    pymodbus clients expose ``connect()`` and
    ``read_holding_registers(address, count, unit=...)`` returning a response
    object with ``.isError()``/``.registers``. modbus_tk instead exposes
    ``open()`` on a ``TcpMaster`` and a generic ``execute(slave, function_code,
    address, count)`` call that raises on failure rather than returning an
    error-checkable object. This wrapper opens the master in place of
    ``connect()`` and translates ``execute()`` calls (and any exceptions they
    raise) into the pymodbus-shaped result so callers are unaffected.
    """

    def __init__(self, master):
        self._master = master

    def connect(self):
        self._master.open()

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst
        import modbus_tk.exceptions as mtk_exceptions

        try:
            registers = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except mtk_exceptions.ModbusError:
            # pymodbus reports a failed read via isError() rather than an
            # exception; reproduce that contract here instead of swallowing
            # the failure.
            return _AxiomModbusResult(error=True)
        return _AxiomModbusResult(registers=registers)


def _axiom_tcpmaster_client_wrapper(host: str, port: int = 502):
    """Build a modbus_tk-backed client that mimics pymodbus's ModbusTcpClient.

    pymodbus's ``ModbusTcpClient(host, port=port)`` followed by ``.connect()``
    yields a client whose ``read_holding_registers`` returns a response object
    with ``.isError()``/``.registers``. modbus_tk instead requires
    constructing a ``TcpMaster(host, port)`` and calling ``.open()``, and its
    ``execute()`` method uses function codes and raises on error. This
    function constructs the TcpMaster, opens the connection, and returns the
    ``_AxiomTcpMasterClient`` shim so the rest of the module can keep using
    the pymodbus-style interface.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host, port)
    client = _AxiomTcpMasterClient(master)
    client.connect()
    return client


def _default_client(host: str, port: int = 502):
    client = _axiom_tcpmaster_client_wrapper(host, port=port)
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
