"""modbus_shim -- glue between the pymodbus-shaped call sites in transport.py
and the modbus-tk client actually used underneath.

Kept as a separate module so the modbus-tk import stays isolated from
transport.py; transport.py only imports the pure-Python shim classes and
factory function from here (which do not themselves require modbus-tk at
import time beyond what this module needs).
"""

import modbus_tk.modbus_tcp as modbus_tcp
import modbus_tk.defines as cst
from modbus_tk.exceptions import ModbusError


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


class _WriteResult:
    """Shim exposing pymodbus's ``.isError()`` response shape for a write."""

    def __init__(self, error=False):
        self._error = error

    def isError(self):
        return self._error


class _ModbusTkClientShim:
    def __init__(self, master):
        self._master = master

    def connect(self):
        return modbus_connect(self._master)

    def read_holding_registers(self, address, count, unit=1):
        return read_holding_registers(self._master, unit, address, count)

    def write_register(self, address, value, unit=1):
        return write_register(self._master, unit, address, value)


def build_modbus_tk_client(host: str, port: int = 502):
    master = modbus_tcp.TcpMaster(host=host, port=port)
    return _ModbusTkClientShim(master)


def modbus_connect(master):
    return master.open()


def read_holding_registers(master, unit, address, count):
    try:
        registers = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
    except (ModbusError, OSError):
        return _RegisterReadResult(registers=None, error=True)
    return _RegisterReadResult(registers=list(registers), error=False)


def write_register(master, unit, address, value):
    try:
        master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value)
    except (ModbusError, OSError):
        return _WriteResult(error=True)
    return _WriteResult(error=False)
