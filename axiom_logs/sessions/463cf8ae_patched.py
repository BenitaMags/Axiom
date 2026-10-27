"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

import modbus_tk.modbus_tcp as modbus_tcp
import modbus_tk.defines as cst
import modbus_tk.exceptions as mtk_exceptions


class _MtkResult:
    """Minimal stand-in for pymodbus's response objects.

    pymodbus's read/write calls return a response object exposing
    `.isError()` (bool) and, for reads, a `.registers` list. modbus-tk's
    `master.execute(...)` instead returns a plain tuple of values on success
    and raises an exception (a `modbus_tk.exceptions.ModbusError` subclass)
    on failure. This tiny object lets the rest of the file keep using the
    pymodbus-style `.isError()` / `.registers` checks unchanged.
    """

    def __init__(self, registers=None, error=None):
        self.registers = list(registers) if registers else []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


def _mtk_read_holding_registers_wrapper(master, unit, address, count):
    """Bridge pymodbus's `read_holding_registers` to modbus-tk's `execute`.

    pymodbus: `client.read_holding_registers(address, count, unit=unit)`
    returns a response object with `.isError()` and `.registers` (a list of
    ints); it does not raise on a normal Modbus exception response.

    modbus-tk: `master.execute(unit, cst.READ_HOLDING_REGISTERS, address,
    count)` returns a plain tuple of register values on success, and raises
    a `modbus_tk.exceptions.ModbusError` (or subclass) on failure instead of
    returning an error flag.

    This wrapper calls `execute()`, catches only modbus-tk's own exception
    type, and repackages the outcome into a `_MtkResult` so existing
    `.isError()` / `.registers[...]` call sites keep working exactly as
    before. The exception is not discarded: `isError()` reporting True is
    the same signal callers already inspect before raising `IOError`, so no
    failure information is lost -- it is translated, not swallowed.
    """
    try:
        values = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
    except mtk_exceptions.ModbusError as exc:
        return _MtkResult(registers=[], error=exc)
    return _MtkResult(registers=values, error=None)


def _mtk_write_register_wrapper(master, unit, address, value):
    """Bridge pymodbus's `write_register` to modbus-tk's `execute`.

    pymodbus: `client.write_register(address, value, unit=unit)` returns a
    response object with `.isError()`.

    modbus-tk: `master.execute(unit, cst.WRITE_SINGLE_REGISTER, address,
    output_value=value)` returns the written value on success and raises a
    `modbus_tk.exceptions.ModbusError` (or subclass) on failure.

    This wrapper calls `execute()`, catches only modbus-tk's own exception
    type, and reports the outcome through the same `.isError()` convention
    the caller already checks, so the caller's existing `IOError`-raising
    logic behaves the same as it did against pymodbus. The exception is
    translated into the `.isError()` signal, never dropped.
    """
    try:
        master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value)
    except mtk_exceptions.ModbusError as exc:
        return _MtkResult(registers=[], error=exc)
    return _MtkResult(registers=[], error=None)


class _ModbusTkClientShim:
    """Exposes a pymodbus-like client surface backed by a modbus_tk TcpMaster.

    Downstream code calls `.connect()`, `.read_holding_registers(...)` and
    `.write_register(...)` in the pymodbus style. This shim forwards those
    calls to the underlying `modbus_tk.modbus_tcp.TcpMaster`, using
    `master.execute()` with explicit function codes as modbus-tk requires.
    """

    def __init__(self, master):
        self._master = master

    def connect(self):
        # pymodbus's connect() opens the socket and returns True/False.
        # modbus-tk's open() performs the same role; it is safe to call
        # again here even though the factory already opened the master.
        self._master.open()
        return True

    def read_holding_registers(self, address, count=1, unit=1):
        return _mtk_read_holding_registers_wrapper(self._master, unit, address, count)

    def write_register(self, address, value, unit=1):
        return _mtk_write_register_wrapper(self._master, unit, address, value)


def _mtk_client_factory_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's `ModbusTcpClient(host, port=port)` + `.connect()`.

    pymodbus: `ModbusTcpClient(host, port=port)` constructs a client object;
    a separate `.connect()` call opens the socket and returns a bool.

    modbus-tk: `modbus_tk.modbus_tcp.TcpMaster(host=host, port=port)`
    constructs a master object whose `.open()` method (not `.connect()`)
    performs the socket connection, raising a modbus_tk exception on
    failure rather than returning a boolean.

    This wrapper constructs the `TcpMaster`, opens it immediately, and
    returns a `_ModbusTkClientShim` exposing pymodbus-style
    `connect()`/`read_holding_registers()`/`write_register()` methods so
    call sites written against pymodbus's API do not need to change their
    shape, only their construction source.
    """
    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.open()
    return _ModbusTkClientShim(master)


def _default_client(host: str, port: int = 502):

    client = _mtk_client_factory_wrapper(host, port)
    client.connect()
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
