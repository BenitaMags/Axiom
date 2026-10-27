"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode

import modbus_tk.modbus_tcp as modbus_tcp
import modbus_tk.defines as cst
import modbus_tk.exceptions as modbus_tk_exceptions


class _AxiomModbusResult:
    """Lightweight stand-in for pymodbus's response object.

    pymodbus's `read_holding_registers()` returns a response object exposing
    `.registers` (a list of ints) and `.isError()` (a bool). modbus-tk's
    `TcpMaster.execute()` instead returns a plain tuple of register values on
    success and raises an exception on failure. This class wraps whichever
    of those two outcomes occurred into the pymodbus-shaped object so
    existing call sites written against pymodbus's API need no changes.
    """

    def __init__(self, registers, error=False):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


class _AxiomModbusClientAdapter:
    """Adapter exposing a pymodbus-like client API backed by modbus-tk.

    pymodbus clients are used as `client.connect()` then
    `client.read_holding_registers(address, count, unit=...)`. modbus-tk's
    `TcpMaster` is opened via `.open()` (done once, up front, by
    `_axiom_modbus_client_wrapper`) and reads are performed via
    `.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)`, which
    raises on error instead of returning an error-flagged object. This
    adapter provides a no-op `connect()` (the master is already open) and a
    `read_holding_registers()` that calls `execute()`, catches modbus-tk's
    exceptions, and returns an `_AxiomModbusResult` so callers written
    against pymodbus's `.registers`/`.isError()` pattern keep working.
    """

    def __init__(self, master):
        self._master = master

    def connect(self):
        # The underlying TcpMaster is already opened by the wrapper that
        # constructed this adapter, so this is a no-op kept only for
        # interface compatibility with pymodbus's client.connect().
        return True

    def read_holding_registers(self, address, count, unit=1):
        try:
            values = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
            return _AxiomModbusResult(list(values), error=False)
        except (
            modbus_tk_exceptions.ModbusError,
            modbus_tk_exceptions.ModbusInvalidResponseError,
        ):
            return _AxiomModbusResult([], error=True)


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's `ModbusTcpClient` construction to modbus-tk.

    pymodbus: `ModbusTcpClient(host, port=port)` followed by `.connect()`.
    modbus-tk: `modbus_tk.modbus_tcp.TcpMaster(host, port=port)` followed by
    `.open()`. This wrapper performs the modbus-tk construction/open steps
    and returns an `_AxiomModbusClientAdapter` around the resulting
    `TcpMaster`, so callers expecting a pymodbus-shaped client (with
    `.connect()` and `.read_holding_registers()` returning `.registers` /
    `.isError()`) continue to work unchanged.
    """
    master = modbus_tcp.TcpMaster(host, port=port)
    master.open()
    return _AxiomModbusClientAdapter(master)


def _default_client(host: str, port: int = 502):

    return _axiom_modbus_client_wrapper(host, port=port)


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self.client.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
