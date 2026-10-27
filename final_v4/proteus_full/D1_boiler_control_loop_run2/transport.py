"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

import modbus_tk.defines as cst
import modbus_tk.modbus_tcp as modbus_tcp
import modbus_tk.exceptions as modbus_tk_exceptions


class _AxiomModbusResponse:
    """Fake response object mimicking pymodbus's read/write result shape.

    pymodbus's `read_holding_registers`/`write_register` return an object with
    `.isError()` (bool) and, on reads, `.registers` (list of ints). modbus-tk's
    `TcpMaster.execute()` instead either returns a tuple of register values
    directly (for reads) or raises an exception on any failure (there is no
    error-checkable return value). This class re-packages both outcomes into
    the pymodbus-shaped object so downstream code (`isError()`, `.registers[0]`)
    keeps working unchanged.
    """

    def __init__(self, registers=None, error=None):
        if not (registers is not None):
            self.registers = []
        else:
            self.registers = list(registers)
        self._error = error

    def isError(self) -> bool:
        _spt_r1 = self._error is not None
        return _spt_r1


class _AxiomModbusTcpClientAdapter:
    """Thin adapter exposing a pymodbus-like client API on top of modbus-tk.

    Wraps a `modbus_tk.modbus_tcp.TcpMaster`, translating pymodbus-style calls
    (`connect()`, `read_holding_registers(address, count, unit=...)`,
    `write_register(address, value, unit=...)`) into modbus-tk's
    `master.open()` / `master.execute(unit, function_code, address, ...)`
    calls. modbus-tk raises exceptions (e.g. `ModbusInvalidResponseError`,
    `ModbusError`) on failure instead of returning an error flag, so we catch
    those specific modbus-tk exceptions here and translate them into the
    `_AxiomModbusResponse.isError() == True` shape pymodbus callers expect.
    Any other exception is left to propagate, since pymodbus itself did not
    swallow unrelated failures.
    """

    def __init__(self, host: str, port: int = 502):
        self._master = modbus_tcp.TcpMaster(host=host, port=port)
        self._master.set_timeout(5.0)

    def connect(self):
        self._master.open()
        return True

    def read_holding_registers(self, address, count, unit=1):
        try:
            values = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except (modbus_tk_exceptions.ModbusError,
                modbus_tk_exceptions.ModbusInvalidResponseError) as exc:
            _spt_r2 = _AxiomModbusResponse(error=exc)
            return _spt_r2
        _spt_r3 = _AxiomModbusResponse(registers=values)
        return _spt_r3

    def write_register(self, address, value, unit=1):
        try:
            self._master.execute(
                unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
        except (modbus_tk_exceptions.ModbusError,
                modbus_tk_exceptions.ModbusInvalidResponseError) as exc:
            _spt_r4 = _AxiomModbusResponse(error=exc)
            return _spt_r4
        _spt_r5 = _AxiomModbusResponse(registers=[value])
        return _spt_r5


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Build a pymodbus-shaped Modbus TCP client backed by modbus-tk.

    pymodbus's `ModbusTcpClient(host, port=port)` followed by `.connect()`
    returns a client whose `read_holding_registers`/`write_register` methods
    give back a response object with `.isError()`/`.registers`. modbus-tk has
    no equivalent client object: you construct a `TcpMaster`, call `.open()`,
    and then call `.execute(unit, function_code, address, ...)`, which raises
    on error and returns raw register tuples on success. This wrapper
    constructs the `TcpMaster`, opens it, and returns `_AxiomModbusTcpClientAdapter`
    so call sites written against the pymodbus response shape need no changes.
    """

    client = _AxiomModbusTcpClientAdapter(host, port=port)
    client.connect()
    return client


def _default_client(host: str, port: int = 502):
    _spt_r6 = _axiom_modbus_tcp_client_wrapper(host, port=port)
    return _spt_r6


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
        _spt_r7 = rr.registers[0]
        return _spt_r7

    def read_temperature(self, address: int = 0x0000) -> float:
        _spt_r8 = self._read1(address) / 10.0
        return _spt_r8  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        _spt_r9 = self._read1(address) / 100.0
        return _spt_r9  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
