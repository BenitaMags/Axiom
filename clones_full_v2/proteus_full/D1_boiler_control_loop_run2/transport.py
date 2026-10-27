"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

import modbus_tk.defines as cst
import modbus_tk.exceptions as mt_exceptions
import modbus_tk.modbus_tcp as modbus_tcp


class _AxiomModbusResult:
    """Minimal stand-in for a pymodbus response object.

    Exposes the two attributes/methods the rest of this file relies on:
    `.registers` (a list of ints) and `.isError()` (a bool-returning method),
    so code written against pymodbus's response shape keeps working even
    though the underlying transaction was actually performed with modbus-tk.
    """

    def __init__(self, registers=None, error=False):
        if not (registers is not None):
            self.registers = []
        else:
            self.registers = registers
        self._error = error

    def isError(self):
        _spt_r1 = self._error
        return _spt_r1


def _axiom_isError_shim(execute_fn):
    """Run a modbus-tk `execute()` call and adapt its error signaling.

    pymodbus never raises on a Modbus-level failure response; instead it
    returns a response object whose `.isError()` is True, and callers in
    this file branch on that. modbus-tk's `TcpMaster.execute()` has no such
    status object -- it raises a `modbus_tk.exceptions.ModbusError` (or a
    subclass thereof) directly when the slave returns an exception response
    or the transaction otherwise fails.

    This shim calls the zero-argument `execute_fn` (which itself wraps a
    single `execute()` invocation) and, if it raises `mt_exceptions.
    ModbusError`, converts that *specific* failure into a `_AxiomModbusResult`
    with `isError()` == True -- exactly the status pymodbus would have
    returned for the same failure, so the existing `if rr.isError(): raise
    IOError(...)` branches keep working unmodified. Any other exception
    (e.g. a socket/connection error) is deliberately not caught here and
    propagates to the caller unchanged, since pymodbus would not have
    swallowed those into an isError() response either.
    """
    try:
        registers = execute_fn()
    except mt_exceptions.ModbusError:
        _spt_r2 = _AxiomModbusResult(registers=[], error=True)
        return _spt_r2
    _spt_r3 = _AxiomModbusResult(registers=list(registers), error=False)
    return _spt_r3


def _axiom_read_holding_registers_wrapper(master, unit, address, count):
    """Bridge pymodbus's `read_holding_registers` to modbus-tk's `execute`.

    pymodbus's `client.read_holding_registers(address, count, unit=unit)`
    returns a response object with `.isError()` and `.registers`. modbus-tk
    has no method of that name; the equivalent call is
    `TcpMaster.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)`,
    which returns a plain tuple of register values on success and raises on
    failure. This wrapper performs that `execute()` call through
    `_axiom_isError_shim` so failures surface as an `isError()`-True result
    (mirroring pymodbus's own status for the same failure) while successes
    are wrapped in a `_AxiomModbusResult` exposing `.registers`, preserving
    the call site's existing shape (`rr.isError()`, `rr.registers[0]`).
    """
    _spt_r4 = _axiom_isError_shim(
        lambda: master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
    )
    return _spt_r4


def _axiom_write_register_wrapper(master, unit, address, value):
    """Bridge pymodbus's `write_register` to modbus-tk's `execute`.

    pymodbus's `client.write_register(address, value, unit=unit)` returns a
    response object with `.isError()`. modbus-tk's equivalent is
    `TcpMaster.execute(unit, cst.WRITE_SINGLE_REGISTER, address,
    output_value=value)`, which returns a tuple on success and raises on
    failure instead of returning an error status. This wrapper runs that
    `execute()` call through `_axiom_isError_shim` so a modbus-tk failure is
    turned into an `isError()`-True result (matching what pymodbus itself
    would have returned for that same failure) without swallowing any other
    exception, keeping the existing `if wr.isError(): raise IOError(...)`
    call site intact.
    """
    _spt_r5 = _axiom_isError_shim(
        lambda: master.execute(
            unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
        )
    )
    return _spt_r5


def _axiom_modbus_connect_wrapper(master):
    """Bridge pymodbus's `client.connect()` to modbus-tk's `master.open()`.

    pymodbus's `ModbusTcpClient.connect()` opens the TCP socket and returns
    True/False on success/failure. modbus-tk's `TcpMaster` uses `.open()` to
    establish the connection, raising a modbus-tk exception (e.g. a socket
    error or `modbus_tk.exceptions.ModbusError`) if the connection cannot be
    established. This wrapper simply calls `master.open()` and lets any
    exception propagate unchanged, so a hard connection failure is reported
    to the caller exactly as it would be from modbus-tk directly -- nothing
    is caught or converted here.
    """
    _spt_r6 = master.open()
    return _spt_r6


class _AxiomModbusClientAdapter:
    """Adapter exposing a pymodbus-`ModbusTcpClient`-like surface over modbus-tk.

    Wraps a `modbus_tk.modbus_tcp.TcpMaster` and exposes `connect()`,
    `read_holding_registers(address, count, unit=1)`, and
    `write_register(address, value, unit=1)` -- the same names/signatures
    pymodbus's client exposed -- so the rest of this file (`ModbusIO`) does
    not need to know that modbus-tk is the library actually doing the work.
    """

    def __init__(self, host, port):
        self._master = modbus_tcp.TcpMaster(host=host, port=port)

    def connect(self):
        _spt_r7 = _axiom_modbus_connect_wrapper(self._master)
        return _spt_r7

    def read_holding_registers(self, address, count, unit=1):
        _spt_r8 = _axiom_read_holding_registers_wrapper(self._master, unit, address, count)
        return _spt_r8

    def write_register(self, address, value, unit=1):
        _spt_r9 = _axiom_write_register_wrapper(self._master, unit, address, value)
        return _spt_r9


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's `ModbusTcpClient(host, port=port)` constructor.

    pymodbus's `ModbusTcpClient(host, port=port)` builds a lazily-connecting
    client object exposing `connect()`, `read_holding_registers()`,
    `write_register()`, and `isError()`-style responses. modbus-tk's
    analogous constructor is `modbus_tk.modbus_tcp.TcpMaster(host, port)`,
    but its resulting object only exposes `open()`/`execute()`, with no
    `isError()` responses. This wrapper constructs the `TcpMaster` and
    returns an `_AxiomModbusClientAdapter` around it, which re-exposes the
    pymodbus-shaped methods (backed internally by
    `_axiom_modbus_connect_wrapper`, `_axiom_read_holding_registers_wrapper`,
    and `_axiom_write_register_wrapper`), so existing call sites in this
    file do not need to change their shape.
    """
    _spt_r10 = _AxiomModbusClientAdapter(host, port)
    return _spt_r10


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_wrapper(host, port)
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
        _spt_r11 = rr.registers[0]
        return _spt_r11

    def read_temperature(self, address: int = 0x0000) -> float:
        _spt_r12 = self._read1(address) / 10.0
        return _spt_r12  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        _spt_r13 = self._read1(address) / 100.0
        return _spt_r13  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
