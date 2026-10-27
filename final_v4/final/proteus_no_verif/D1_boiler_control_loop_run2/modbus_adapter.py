"""Adapter layer mapping the pymodbus-style client surface onto modbus-tk.

This module isolates the glue needed because the underlying transport
library (modbus-tk) exposes a different connection and call API than the
pymodbus surface the rest of the codebase is written against.
"""


def build_tcp_client(host: str, port: int = 502):
    """Build a modbus-tk TCP master adapted to the pymodbus client surface.

    pymodbus's ModbusTcpClient exposes .connect()/.close() for connection
    management and .read_holding_registers()/.write_register() as direct
    methods returning response objects. modbus-tk's TcpMaster instead uses
    .open()/.close() for connections and a single .execute(...) method
    parameterized by a modbus_tk.defines function-code constant for every
    read/write operation, returning plain tuples (or raising) rather than
    response objects. This factory wraps a TcpMaster in a small adapter class
    that maps .connect() -> .open() and delegates read_holding_registers()/
    write_register() to the pseudo-response wrappers below, so the existing
    call sites written against the pymodbus surface keep working unchanged.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    return _TcpClientAdapter(modbus_tcp, host, port)


class _PseudoResponse:
    """Common shape for the pseudo-response objects returned by the adapter."""

    def __init__(self, error=False, registers=None):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _TcpClientAdapter:
    def __init__(self, modbus_tcp_module, host, port):
        self._master = modbus_tcp_module.TcpMaster(host=host, port=port)

    def connect(self):
        self._master.open()

    def close(self):
        self._master.close()

    def read_holding_registers(self, address, count, unit=1):
        return _execute_read(self._master, address, count, unit)

    def write_register(self, address, value, unit=1):
        return _execute_write(self._master, address, value, unit)


def _failure_exceptions():
    """Return the exception types that signal a failed modbus-tk call.

    Imported lazily so this module can be loaded without modbus_tk present.
    """
    import socket
    import struct

    import modbus_tk.exceptions as modbus_tk_exceptions

    return (modbus_tk_exceptions.ModbusError, socket.error, struct.error)


def _execute_read(master, address, count, unit):
    """Adapt modbus-tk's execute() to pymodbus's read_holding_registers() contract.

    pymodbus's read_holding_registers() returns a response object exposing
    .isError() (bool) and .registers (list of ints) on both success and
    failure -- it does not raise for a normal Modbus exception response.
    modbus-tk's Master.execute(unit, cst.READ_HOLDING_REGISTERS, address,
    count) instead returns a plain tuple of ints on success and *raises* a
    modbus_tk exception (protocol errors) or a socket/struct error
    (transport errors, timeouts, malformed responses) on failure. This
    wrapper performs the call, catches those failure modes, and packages
    the outcome into a small pseudo-response object with .isError() and
    .registers so callers written against the pymodbus surface need no
    changes.
    """
    import modbus_tk.defines as cst

    try:
        result = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
    except _failure_exceptions():
        return _PseudoResponse(error=True)
    return _PseudoResponse(error=False, registers=result)


def _execute_write(master, address, value, unit):
    """Adapt modbus-tk's execute() to pymodbus's write_register() contract.

    pymodbus's write_register() returns a response object exposing
    .isError() (bool) on both success and failure -- it does not raise for
    a normal Modbus exception response. modbus-tk's Master.execute(unit,
    cst.WRITE_SINGLE_REGISTER, address, output_value=value) instead returns
    a plain tuple on success and *raises* a modbus_tk exception (protocol
    errors) or a socket/struct error (transport errors, timeouts,
    malformed responses) on failure. This wrapper performs the call,
    catches those failure modes, and packages the outcome into a small
    pseudo-response object with .isError() so callers written against the
    pymodbus surface need no changes.
    """
    import modbus_tk.defines as cst

    try:
        master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value)
    except _failure_exceptions():
        return _PseudoResponse(error=True)
    return _PseudoResponse(error=False)
