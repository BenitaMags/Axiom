"""mbtk_adapter -- glue between the pymodbus-shaped API `ModbusIO` expects
and the modbus_tk client library actually used underneath.

Kept separate from `transport` so the pieces that talk to modbus_tk
directly can be swapped or tested independently of the field-I/O logic.
"""


class MbtkResponse:
    """Pseudo-response object mimicking pymodbus's response objects.

    pymodbus's read/write calls return an object exposing `.isError()`
    and, for reads, `.registers` (a list of ints). modbus_tk's
    `master.execute()` instead either raises an exception on failure or
    returns a plain tuple of register values on success. This class
    wraps that tuple (or an error flag) so call sites written against
    the pymodbus response shape keep working unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


def _execute_guarded(master, unit, function_code, address, **kwargs):
    """Run `master.execute(...)`, translating the modbus_tk failure
    modes (`ModbusError` and subclasses, or `OSError`/timeouts) into a
    `None` sentinel instead of letting them propagate. Callers turn the
    sentinel into the appropriate pseudo-response.
    """
    import modbus_tk.exceptions as mbtk_exceptions

    try:
        return master.execute(unit, function_code, address, **kwargs), None
    except (mbtk_exceptions.ModbusError, OSError):
        return None, True


def read_holding_registers(master, unit, address, count):
    """pymodbus's `read_holding_registers(address, count, unit=...)`
    returns a response object with `.isError()` and `.registers` (a list
    of ints); it reports both protocol errors (illegal address, etc.)
    and transport errors (connection refused, timeouts) as an
    error-flagged response object rather than raising. modbus_tk's
    `master.execute(slave, cst.READ_HOLDING_REGISTERS, address, count)`
    instead returns a raw tuple of register values on success and
    RAISES on any of those same failure modes: `modbus_tk.exceptions.
    ModbusError` (and its subclasses) for protocol-level failures, and
    `OSError`/`socket.timeout` for transport failures. This wrapper
    calls `execute()`, catches exactly those exception classes, and
    synthesizes a pseudo-response so the call site's
    `rr.isError()` / `rr.registers[0]` logic is unchanged.
    """
    import modbus_tk.defines as cst

    registers, errored = _execute_guarded(
        master, unit, cst.READ_HOLDING_REGISTERS, address, count=count
    )
    if errored:
        return MbtkResponse(error=True)
    return MbtkResponse(registers=registers, error=False)


def write_register(master, unit, address, value):
    """pymodbus's `write_register(address, value, unit=...)` returns a
    response object with `.isError()`, reporting both protocol errors
    and transport errors (connection refused, timeouts) via that flag
    rather than raising. modbus_tk's `master.execute(slave,
    cst.WRITE_SINGLE_REGISTER, address, output_value=value)` returns
    plain data on success and RAISES `modbus_tk.exceptions.ModbusError`
    (and subclasses) for protocol failures, or `OSError`/`socket.timeout`
    for transport failures. This wrapper calls `execute()`, catches
    exactly those exception classes, and returns a pseudo-response
    exposing `.isError()` so the call site's `wr.isError()` check keeps
    working unchanged.
    """
    import modbus_tk.defines as cst

    _, errored = _execute_guarded(
        master, unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
    )
    return MbtkResponse(error=bool(errored))


class MbtkClientAdapter:
    """Adapts a modbus_tk `TcpMaster` to the small subset of the pymodbus
    `ModbusTcpClient` API that `ModbusIO` relies on: `connect()`,
    `read_holding_registers()`, and `write_register()`. pymodbus's
    `ModbusTcpClient.connect()` opens the TCP socket; modbus_tk's
    `TcpMaster.open()` performs the same job under a different name, so
    `connect()` here forwards to `open()` to preserve the connect/close
    lifecycle `ModbusIO` expects.
    """

    def __init__(self, master):
        self._master = master

    def connect(self):
        return self._master.open()

    def close(self):
        return self._master.close()

    def read_holding_registers(self, address, count, unit=1):
        return read_holding_registers(self._master, unit, address, count)

    def write_register(self, address, value, unit=1):
        return write_register(self._master, unit, address, value)


def _new_tcp_master(host, port):
    """Build a modbus_tk `TcpMaster` for `host`/`port`, wrapped so it
    exposes the pymodbus-like `connect()` / `read_holding_registers()` /
    `write_register()` surface that `ModbusIO` was written against.
    pymodbus's `ModbusTcpClient(host, port=port)` builds a client object
    without opening the socket until `.connect()` is called; modbus_tk's
    `TcpMaster` follows the same open-after-construct model via
    `.open()`, so this factory returns an adapter whose `connect()`
    calls `TcpMaster.open()`.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)
    return MbtkClientAdapter(master)


def build_default_client(host: str, port: int = 502):
    client = _new_tcp_master(host, port)
    client.connect()
    return client
