"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


class _AxiomModbusResponse:
    """Minimal stand-in for a pymodbus response object.

    pymodbus's read/write calls return a response object exposing
    ``.isError()`` and, for reads, ``.registers`` (a list of ints).
    modbus_tk's ``Master.execute()`` instead either returns a plain tuple of
    register values on success or raises a ``modbus_tk.exceptions`` subclass
    (e.g. ``ModbusInvalidResponseError``, ``ModbusError``) on failure. This
    object re-wraps whichever of those two outcomes actually happened so the
    existing call sites (``rr.isError()`` / ``rr.registers[0]`` /
    ``wr.isError()``) keep working unchanged.
    """

    def __init__(self, registers=None, error=False):
        if not (registers is not None):
            self.registers = []
        else:
            self.registers = list(registers)
        self._error = error

    def isError(self) -> bool:
        _spt_r1 = self._error
        return _spt_r1


def _axiom_read_holding_registers_wrapper(master, address, count, unit):
    """Bridge pymodbus's ``read_holding_registers`` to modbus_tk's ``execute``.

    pymodbus: ``client.read_holding_registers(address, count, unit=unit)``
    returns a response object with ``.isError()`` and ``.registers``.

    modbus_tk: ``master.execute(unit, defines.READ_HOLDING_REGISTERS,
    address, count)`` returns a plain tuple of register values on success,
    and raises a modbus_tk exception (e.g. ``ModbusError``,
    ``ModbusInvalidResponseError``) on failure -- it never returns an error
    flag.

    This wrapper calls ``execute`` and converts each of those two outcomes
    into a ``_AxiomModbusResponse`` so callers can keep doing
    ``rr.isError()`` / ``rr.registers[0]``. The modbus_tk exception is not
    swallowed silently: it is what drives ``isError()`` to True, which is
    exactly the same signal pymodbus callers already checked for.
    """
    import modbus_tk.defines as defines
    import modbus_tk.exceptions as mt_exceptions

    try:
        result = master.execute(unit, defines.READ_HOLDING_REGISTERS, address, count)
    except mt_exceptions.ModbusError:
        _spt_r2 = _AxiomModbusResponse(registers=None, error=True)
        return _spt_r2
    _spt_r3 = _AxiomModbusResponse(registers=result, error=False)
    return _spt_r3


def _axiom_write_register_wrapper(master, address, value, unit):
    """Bridge pymodbus's ``write_register`` to modbus_tk's ``execute``.

    pymodbus: ``client.write_register(address, value, unit=unit)`` returns a
    response object with ``.isError()``.

    modbus_tk: ``master.execute(unit, defines.WRITE_SINGLE_REGISTER,
    address, output_value=value)`` returns the written value/tuple on
    success and raises a modbus_tk exception on failure, with no error
    flag of its own.

    This wrapper performs the ``execute`` call and converts a raised
    modbus_tk exception into ``isError() == True`` (and a clean call into
    ``isError() == False``), preserving the ``wr.isError()`` check used by
    callers without inventing any new status codes.
    """

    try:
        master.execute(
            unit, defines.WRITE_SINGLE_REGISTER, address, output_value=value
        )
    except mt_exceptions.ModbusError:
        _spt_r4 = _AxiomModbusResponse(registers=None, error=True)
        return _spt_r4
    _spt_r5 = _AxiomModbusResponse(registers=None, error=False)
    return _spt_r5


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's ``ModbusTcpClient`` to modbus_tk's ``TcpMaster``.

    pymodbus: ``ModbusTcpClient(host, port=port)`` returns a client object
    with ``.connect()`` (which opens the socket), plus
    ``.read_holding_registers(...)`` / ``.write_register(...)`` methods that
    return response objects.

    modbus_tk: connections are made through
    ``modbus_tk.modbus_tcp.TcpMaster(host=host, port=port)``, which is
    opened via ``.open()`` instead of ``.connect()``, and reads/writes go
    through the single ``execute()`` method with function-code constants
    from ``modbus_tk.defines`` rather than per-function methods.

    This factory builds a ``TcpMaster`` and returns a small shim exposing
    the pymodbus-shaped surface (``connect()``, ``read_holding_registers()``,
    ``write_register()``) that ``ModbusIO`` already calls, so no call site
    needs to know modbus_tk's API. ``connect()`` maps onto ``TcpMaster.open()``
    to preserve the original connect-then-use lifecycle.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)

    class _AxiomModbusClientShim:
        def __init__(self, master):
            self._master = master

        def connect(self):
            self._master.open()
            return True

        def read_holding_registers(self, address, count, unit=1):
            _spt_r6 = _axiom_read_holding_registers_wrapper(
                self._master, address, count, unit
            )
            return _spt_r6

        def write_register(self, address, value, unit=1):
            _spt_r7 = _axiom_write_register_wrapper(
                self._master, address, value, unit
            )
            return _spt_r7

        def close(self):
            self._master.close()
    _spt_r8 = _AxiomModbusClientShim(master)

    return _spt_r8


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_client_wrapper(host, port=port)
    client.connect()
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if not (client is not None):
            self.client = _default_client(host)
        else:
            self.client = client
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        _spt_r9 = rr.registers[0]
        return _spt_r9

    def read_temperature(self, address: int = 0x0000) -> float:
        _spt_r10 = self._read1(address) / 10.0
        return _spt_r10  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        _spt_r11 = self._read1(address) / 100.0
        return _spt_r11  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
