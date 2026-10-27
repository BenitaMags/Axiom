"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

import modbus_tk.modbus_tcp as modbus_tcp
import modbus_tk.defines as cst
import modbus_tk.exceptions as mbtk_exceptions


class _MbtkResponse:
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


class _MbtkClientAdapter:
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
        return _axiom_read_holding_registers_wrapper(self._master, unit, address, count)

    def write_register(self, address, value, unit=1):
        return _axiom_write_register_wrapper(self._master, unit, address, value)


def _mbtk_client_factory(host, port=502):
    """Build a modbus_tk `TcpMaster` for `host`/`port` and wrap it so it
    exposes the pymodbus-like `connect()` / `read_holding_registers()` /
    `write_register()` surface that `ModbusIO` was written against.
    pymodbus's `ModbusTcpClient(host, port=port)` builds a client object
    without opening the socket until `.connect()` is called; modbus_tk's
    `TcpMaster` follows the same open-after-construct model via
    `.open()`, so this factory returns an adapter whose `connect()`
    calls `TcpMaster.open()`.
    """
    master = modbus_tcp.TcpMaster(host=host, port=port)
    return _MbtkClientAdapter(master)


def _axiom_read_holding_registers_wrapper(master, unit, address, count):
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
    try:
        registers = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
    except (mbtk_exceptions.ModbusError, OSError):
        return _MbtkResponse(error=True)
    return _MbtkResponse(registers=registers, error=False)


def _axiom_write_register_wrapper(master, unit, address, value):
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
    try:
        master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value)
    except (mbtk_exceptions.ModbusError, OSError):
        return _MbtkResponse(error=True)
    return _MbtkResponse(error=False)


def _default_client(host: str, port: int = 502):

    client = _mbtk_client_factory(host, port)
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
