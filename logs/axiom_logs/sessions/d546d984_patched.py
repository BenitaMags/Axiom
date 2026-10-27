"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode

import modbus_tk.defines as cst
import modbus_tk.modbus as modbus_tk_modbus
from modbus_tk.modbus_tcp import TcpMaster


class _ModbusTkResult:
    """Adapter exposing the same shape callers expect from a pymodbus response.

    pymodbus's `read_holding_registers` returns a response object with
    `.registers` (a list of ints) and `.isError()` (a bool). modbus-tk's
    `TcpMaster.execute(...)` instead returns a plain tuple of register
    values directly, and signals failure by *raising* a modbus_tk exception
    rather than returning an error-flagged object. This wrapper stores the
    raised exception (if any) and the raw tuple (if the call succeeded), and
    exposes `.registers` (converted to a list) and `.isError()` so downstream
    code (which calls `result.isError()` then `result.registers`) is
    unchanged.
    """

    def __init__(self, registers=None, error=None):
        self._error = error
        self.registers = list(registers) if registers is not None else []

    def isError(self):
        return self._error is not None


class _ModbusTkClientAdapter:
    """Adapter around modbus_tk.modbus_tcp.TcpMaster mimicking pymodbus's client.

    pymodbus's `ModbusTcpClient` exposes `.connect()` and
    `.read_holding_registers(address, count, unit=...)` returning a response
    object with `.isError()`/`.registers`. modbus-tk's `TcpMaster` instead
    uses `.open()` to connect and `.execute(unit, function_code, address,
    count)` to read registers, returning a raw tuple and raising a
    modbus_tk exception (subclass of `modbus_tk.modbus.ModbusError`) on
    failure. This adapter calls `.open()` in place of `.connect()`, and
    wraps `.execute(...)` so that a failure is caught and turned into a
    `_ModbusTkResult` with `.isError() == True` (emulating pymodbus's error
    object) instead of propagating as an uncaught exception -- matching the
    original error-checking contract used by callers (`if result.isError():
    raise IOError(...)`).
    """

    def __init__(self, master):
        self._master = master

    def read_holding_registers(self, address, count, unit=1):
        try:
            values = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except modbus_tk_modbus.ModbusError as exc:
            return _ModbusTkResult(error=exc)
        return _ModbusTkResult(registers=values)


def _modbus_tk_client_wrapper(host: str, port: int = 502):
    """Build a modbus-tk based client adapter matching pymodbus's client API.

    pymodbus's `ModbusTcpClient(host, port=port)` is constructed and then
    `.connect()`ed. modbus-tk's equivalent is `TcpMaster(host, port=port)`
    followed by `.open()`. This wrapper performs that construction/opening
    and returns a `_ModbusTkClientAdapter` so callers can keep using
    `.read_holding_registers(address, count, unit=...)` and
    `result.isError()`/`result.registers` unchanged.
    """
    master = TcpMaster(host=host, port=port)
    master.open()
    return _ModbusTkClientAdapter(master)


def _default_client(host: str, port: int = 502):
    return _modbus_tk_client_wrapper(host, port=port)


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
