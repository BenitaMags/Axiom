"""Helper shims bridging modbus-tk's API to the pymodbus-shaped surface
that ``ModbusIO`` (in ``transport.py``) was originally written against.
"""


class _ModbusRegisterReadResult:
    """Shim mimicking pymodbus's read-holding-registers response object.

    pymodbus's ``read_holding_registers`` returns an object exposing
    ``.isError()`` and ``.registers`` (a list of ints); it never raises for a
    protocol or transport failure, callers just check ``isError()``.
    modbus-tk's ``Master.execute`` instead returns a plain tuple of register
    values on success and RAISES an exception on failure. This class is the
    pymodbus-shaped container that ``_mtk_read_holding_registers_wrapper``
    fills in either case, so existing ``isError()``/``registers`` call sites
    keep working unmodified.
    """

    def __init__(self, registers=None, error=False):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _ModbusWriteResult:
    """Shim mimicking pymodbus's write-single-register response object.

    pymodbus's ``write_register`` returns an object exposing ``.isError()``
    (the only attribute the existing call sites use) and never raises for a
    protocol/transport failure. modbus-tk's ``Master.execute`` instead
    returns the echoed value on success and RAISES on failure. This class is
    the pymodbus-shaped container ``_mtk_write_register_wrapper`` fills in
    either case.
    """

    def __init__(self, error=False):
        self._error = error

    def isError(self) -> bool:
        return self._error


def _mtk_read_holding_registers_wrapper(master, cst_module, mtk_exceptions, address, count, unit):
    """
    pymodbus's ``client.read_holding_registers(address, count, unit=...)``
    returns a response object with ``.isError()`` and ``.registers`` (a list
    of ints), never raising for a protocol/transport failure -- the caller
    checks ``isError()``. modbus-tk's
    ``master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)``
    instead returns a plain tuple of register values on success and RAISES a
    modbus_tk exception on failure: protocol failures come out of
    ``modbus_tk.exceptions.ModbusError`` (and its subclasses such as
    ``ModbusInvalidResponseError``/``ModbusFunctionNotSupportedError``), while
    transport failures (connection refused, timeouts, short reads) surface as
    ``socket.error``/``socket.timeout`` from the underlying TCP socket. This
    wrapper calls ``execute()``, catches that combined set (matching every
    failure mode pymodbus used to report through ``isError()``), and returns
    a ``_ModbusRegisterReadResult`` so ``ModbusIO._read1``'s
    ``isError()``/``registers`` chain keeps working without modification.
    """
    import socket

    try:
        values = master.execute(unit, cst_module.READ_HOLDING_REGISTERS, address, count)
        return _ModbusRegisterReadResult(registers=values, error=False)
    except (mtk_exceptions.ModbusError, socket.error, socket.timeout):
        return _ModbusRegisterReadResult(registers=[], error=True)


def _mtk_write_register_wrapper(master, cst_module, mtk_exceptions, address, value, unit):
    """
    pymodbus's ``client.write_register(address, value, unit=...)`` returns a
    response object with ``.isError()``, never raising for a protocol or
    transport failure. modbus-tk's
    ``master.execute(unit, cst.WRITE_SINGLE_REGISTER, address,
    output_value=value)`` returns the echoed value on success and RAISES a
    modbus_tk exception (``modbus_tk.exceptions.ModbusError`` and its
    subclasses) or a ``socket.error``/``socket.timeout`` on transport failure
    instead. This wrapper performs the write, catches that combined set
    (covering the same protocol and transport failures pymodbus reported
    through ``isError()``), and returns a ``_ModbusWriteResult`` so
    ``ModbusIO.write_valve``'s ``isError()`` check keeps working unmodified.
    """
    import socket

    try:
        master.execute(unit, cst_module.WRITE_SINGLE_REGISTER, address, output_value=value)
        return _ModbusWriteResult(error=False)
    except (mtk_exceptions.ModbusError, socket.error, socket.timeout):
        return _ModbusWriteResult(error=True)


class _ModbusTkClientShim:
    """Bridges ``modbus_tk.modbus_tcp.TcpMaster`` to the pymodbus
    ``ModbusTcpClient`` surface (``connect``/``read_holding_registers``/
    ``write_register``) that ``ModbusIO`` was written against, so ``ModbusIO``
    itself needs no further changes.
    """

    def __init__(self, host, port, modbus_tcp_module):
        import modbus_tk.defines as cst  # lazy import for testability
        import modbus_tk.exceptions as mtk_exceptions  # lazy import for testability

        self._cst = cst
        self._exceptions = mtk_exceptions
        self._master = modbus_tcp_module.TcpMaster(host=host, port=port)
        self._master.set_timeout(5.0)

    def connect(self):
        """
        pymodbus's ``connect()`` opens the TCP socket and returns a
        True/False success flag. modbus-tk's ``TcpMaster`` normally opens its
        socket lazily on the first ``execute()`` call rather than via an
        explicit connect step. We call ``master.open()`` here eagerly to
        preserve the original connect-then-use sequence the caller relies on,
        and return True to mirror pymodbus's boolean result.
        """
        self._master.open()
        return True

    def read_holding_registers(self, address, count, unit=1):
        return _mtk_read_holding_registers_wrapper(
            self._master, self._cst, self._exceptions, address, count, unit
        )

    def write_register(self, address, value, unit=1):
        return _mtk_write_register_wrapper(
            self._master, self._cst, self._exceptions, address, value, unit
        )


def _mtk_client_factory(host: str, port: int = 502):
    """
    pymodbus's ``_default_client`` built a ``ModbusTcpClient`` and called
    ``.connect()`` on it, returning an object whose ``read_holding_registers``/
    ``write_register`` methods hand back ``.isError()``/``.registers``-shaped
    responses. modbus-tk has no equivalent single client class: it exposes a
    ``modbus_tk.modbus_tcp.TcpMaster`` whose ``execute()`` takes explicit
    function codes and returns raw tuples or raises on failure. This factory
    builds a ``_ModbusTkClientShim`` around a ``TcpMaster`` that re-exposes
    ``connect``/``read_holding_registers``/``write_register`` with
    pymodbus-shaped results, so ``ModbusIO`` can keep using the same client
    surface unmodified.
    """
    import modbus_tk.modbus_tcp as modbus_tcp  # lazy import for testability

    return _ModbusTkClientShim(host, port, modbus_tcp)


def _default_client(host: str, port: int = 502):

    client = _mtk_client_factory(host, port)
    client.connect()
    return client
