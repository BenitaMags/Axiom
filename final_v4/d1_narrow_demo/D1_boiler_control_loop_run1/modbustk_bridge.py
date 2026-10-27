"""modbustk_bridge -- adapts modbus_tk's TcpMaster to a pymodbus-shaped API.

pymodbus's client exposes:
    client.connect() -> bool
    client.read_holding_registers(address, count, unit=unit) -> response
        response.isError() -> bool
        response.registers -> list[int]
    client.write_register(address, value, unit=unit) -> response
        response.isError() -> bool

modbus_tk's TcpMaster instead exposes:
    master.open() -> None (raises on failure)
    master.execute(unit, function_code, address, count) -> tuple[int, ...]
        (raises modbus_tk.exceptions.ModbusError on failure)
    master.execute(unit, function_code, address, output_value=value)
        (raises modbus_tk.exceptions.ModbusError on failure)

Everything in this module exists to make a TcpMaster answer to the
pymodbus-shaped calls above, without altering any observable behaviour:
failures still surface as `.isError() == True`, successes still surface
register lists / written values the same way.
"""

import modbus_tk.modbus_tcp as modbus_tcp
import modbus_tk.defines as cst
import modbus_tk.exceptions as mtk_exceptions


class ModbusOutcome:
    """Minimal stand-in for pymodbus's response objects.

    pymodbus's read/write calls return a response object exposing
    `.isError()` (bool) and, for reads, a `.registers` list. modbus-tk's
    `master.execute(...)` instead returns a plain tuple of values on success
    and raises an exception (a `modbus_tk.exceptions.ModbusError` subclass)
    on failure. This object lets callers keep using the pymodbus-style
    `.isError()` / `.registers` checks unchanged.
    """

    __slots__ = ("registers", "_error")

    def __init__(self, registers=None, error=None):
        self.registers = list(registers) if registers else []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


def _execute(master, unit, function_code, *args, **kwargs):
    """Run one modbus_tk `execute()` call, translating its exception model.

    modbus_tk raises `ModbusError` (or a subclass) on failure instead of
    returning an error flag like pymodbus does. This helper catches only
    that exception type and reports the outcome through a `ModbusOutcome`,
    so the failure is translated -- never silently dropped.
    """
    try:
        result = master.execute(unit, function_code, *args, **kwargs)
    except mtk_exceptions.ModbusError as exc:
        return ModbusOutcome(registers=[], error=exc)
    return result


def read_holding_registers(master, unit, address, count):
    """Bridge pymodbus's `read_holding_registers` to modbus-tk's `execute`."""
    result = _execute(master, unit, cst.READ_HOLDING_REGISTERS, address, count)
    if isinstance(result, ModbusOutcome):
        return result
    return ModbusOutcome(registers=result, error=None)


def write_register(master, unit, address, value):
    """Bridge pymodbus's `write_register` to modbus-tk's `execute`."""
    result = _execute(
        master, unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
    )
    if isinstance(result, ModbusOutcome):
        return result
    return ModbusOutcome(registers=[], error=None)


class TcpMasterClient:
    """Exposes a pymodbus-like client surface backed by a modbus_tk TcpMaster.

    Downstream code calls `.connect()`, `.read_holding_registers(...)` and
    `.write_register(...)` in the pymodbus style. This adapter forwards
    those calls to the underlying `modbus_tk.modbus_tcp.TcpMaster`, using
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
        return read_holding_registers(self._master, unit, address, count)

    def write_register(self, address, value, unit=1):
        return write_register(self._master, unit, address, value)


def build_default_client(host: str, port: int = 502):
    """Bridge pymodbus's `ModbusTcpClient(host, port=port)` + `.connect()`.

    pymodbus: `ModbusTcpClient(host, port=port)` constructs a client object;
    a separate `.connect()` call opens the socket and returns a bool.

    modbus-tk: `modbus_tk.modbus_tcp.TcpMaster(host=host, port=port)`
    constructs a master object whose `.open()` method (not `.connect()`)
    performs the socket connection, raising a modbus_tk exception on
    failure rather than returning a boolean.

    This constructs the `TcpMaster`, opens it immediately, and returns a
    `TcpMasterClient` exposing pymodbus-style
    `connect()`/`read_holding_registers()`/`write_register()` methods, then
    connects it -- mirroring the original two-step pymodbus construction.
    """
    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.open()
    client = TcpMasterClient(master)
    client.connect()
    return client
