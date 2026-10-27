"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

import modbus_tk.modbus_tcp as modbus_tcp
import modbus_tk.defines as cst
from modbus_tk.exceptions import ModbusError


def _axiom_modbus_tcp_client_factory(host: str, port: int = 502):
    """Build a modbus-tk TcpMaster wrapped in a pymodbus-ModbusTcpClient-like shim.

    pymodbus's ``ModbusTcpClient(host, port=port)`` returns a client object
    exposing ``connect()``, ``read_holding_registers(address, count,
    unit=...)`` and ``write_register(address, value, unit=...)``, where the
    read/write calls return response objects with ``.isError()`` and (on
    read) ``.registers``.

    modbus-tk instead exposes ``modbus_tk.modbus_tcp.TcpMaster``, whose
    connection method is ``open()`` (not ``connect()``) and whose I/O method
    is a single ``execute(slave_id, function_code, address, ...)`` call that
    returns a plain tuple on success and raises a
    ``modbus_tk.exceptions.ModbusError`` (or an ``OSError``/socket error for
    transport failures) instead of returning a status object.

    This factory builds the TcpMaster and returns a small shim object that
    re-exposes the pymodbus method names on top of it, via the
    ``_axiom_modbus_connect_wrapper``, ``_axiom_read_holding_registers_wrapper``
    and ``_axiom_write_register_wrapper`` helpers below, so existing calling
    code written against the pymodbus interface keeps working unchanged.
    """

    master = modbus_tcp.TcpMaster(host=host, port=port)

    class _ModbusTkClientShim:
        def __init__(self, master):
            self._master = master

        def connect(self):
            return _axiom_modbus_connect_wrapper(self._master)

        def read_holding_registers(self, address, count, unit=1):
            return _axiom_read_holding_registers_wrapper(self._master, unit, address, count)

        def write_register(self, address, value, unit=1):
            return _axiom_write_register_wrapper(self._master, unit, address, value)

    return _ModbusTkClientShim(master)


def _axiom_modbus_connect_wrapper(master):
    """Translate pymodbus's ``client.connect()`` into modbus-tk's ``master.open()``.

    pymodbus's ``connect()`` opens the TCP socket (returning a bool that
    this codebase never checked). modbus-tk's equivalent lifecycle method is
    ``TcpMaster.open()``, which opens the socket and raises on failure
    instead of returning a boolean. We call it directly, unguarded, so a
    connection failure still surfaces loudly (as an exception) exactly as a
    later failed read/write would have under pymodbus.
    """
    return master.open()


class _RegisterReadResult:
    """Shim exposing pymodbus's ``.registers``/``.isError()`` response shape.

    modbus-tk's ``execute()`` returns a plain tuple of register values on
    success and raises on failure, rather than returning an object with
    ``.isError()``/``.registers``. This shim reproduces that pymodbus shape
    so callers written as ``if rr.isError(): ... ; rr.registers[0]``
    continue to work unmodified.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


def _axiom_read_holding_registers_wrapper(master, unit, address, count):
    """Read holding registers, bridging modbus-tk's execute() to pymodbus's API.

    pymodbus's ``read_holding_registers(address, count, unit=...)`` returns
    a response object; callers check ``.isError()`` and, if False, read
    ``.registers``.

    modbus-tk's ``TcpMaster.execute(slave_id, cst.READ_HOLDING_REGISTERS,
    address, count)`` returns a plain tuple of register values on success,
    and raises a ``modbus_tk.exceptions.ModbusError`` on protocol failures
    (bad function/exception responses, malformed frames) or an ``OSError``
    (covering socket timeouts, connection resets, connection refused) on
    transport failures -- these are siblings, not a single hierarchy, so
    both are caught here since pymodbus reported both kinds of failure
    through the same ``isError()`` status.

    We catch exactly those two failure classes and convert them into a
    ``_RegisterReadResult`` with ``isError() == True``, mirroring what
    pymodbus itself did for these same failure modes (return a status
    object rather than raise). Any other, unexpected exception is not
    caught here and propagates to the caller, since pymodbus never silently
    absorbed unrelated failures either.
    """
    try:
        registers = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
    except (ModbusError, OSError):
        return _RegisterReadResult(registers=None, error=True)
    return _RegisterReadResult(registers=list(registers), error=False)


class _WriteResult:
    """Shim exposing pymodbus's ``.isError()`` response shape for a write."""

    def __init__(self, error=False):
        self._error = error

    def isError(self):
        return self._error


def _axiom_write_register_wrapper(master, unit, address, value):
    """Write a single holding register, bridging modbus-tk to pymodbus's API.

    pymodbus's ``write_register(address, value, unit=...)`` returns a
    response object whose ``.isError()`` reports success/failure.

    modbus-tk's ``TcpMaster.execute(slave_id, cst.WRITE_SINGLE_REGISTER,
    address, output_value=value)`` performs the write and raises a
    ``modbus_tk.exceptions.ModbusError`` (protocol failure) or an
    ``OSError`` (transport failure: timeout, reset, refused) instead of
    returning a status object -- both are caught here because pymodbus
    surfaced both kinds of failure the same way, through ``isError()``.

    On success we return a ``_WriteResult`` with ``isError() == False``. On
    one of the two caught failure classes we return ``isError() == True``,
    reproducing pymodbus's own status-based reporting for this exact
    failure rather than inventing a new code. Any other exception is left
    to propagate, since pymodbus did not swallow unrelated errors either.
    """
    try:
        master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value)
    except (ModbusError, OSError):
        return _WriteResult(error=True)
    return _WriteResult(error=False)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_tcp_client_factory(host, port)
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
