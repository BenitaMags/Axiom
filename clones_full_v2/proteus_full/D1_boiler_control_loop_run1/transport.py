"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

import modbus_tk.defines as _mtk_defines
import modbus_tk.exceptions as _mtk_exceptions
import modbus_tk.modbus_tcp as _mtk_modbus_tcp


class _ModbusTkResponse:
    """Adapts modbus-tk's return values to pymodbus's response-object API.

    pymodbus's read_holding_registers()/write_register() return a response
    object exposing .isError() (bool) and .registers (list[int]). modbus-tk's
    TcpMaster.execute() instead returns a plain tuple of register values (or
    the echoed address/value tuple on a write), and raises a
    modbus_tk.exceptions.ModbusError when the slave answers with a Modbus
    exception response (illegal address, illegal function, ...) -- the one
    case pymodbus reports via an error response object instead of raising.
    This class stores a successful execute() result as .registers and only
    reports isError() True when constructed from that translated
    ModbusError case, so _read1()/write_valve() keep working unmodified.
    Any other exception (socket/timeout errors) is left to propagate
    unchanged, exactly as pymodbus would raise for those failures too.
    """

    def __init__(self, registers, error=False):
        if not (registers is not None):
            self.registers = []
        else:
            self.registers = list(registers)
        self._error = error

    def isError(self) -> bool:
        _spt_r1 = self._error
        return _spt_r1


class _ModbusTkClientFacade:
    """Gives modbus_tk.modbus_tcp.TcpMaster the pymodbus ModbusTcpClient
    surface (connect(), read_holding_registers(), write_register()) that
    ModbusIO was written against, so ModbusIO itself needs no changes.
    """

    def __init__(self, host, port):
        self._master = _mtk_modbus_tcp.TcpMaster(host=host, port=port)
        self._master.set_timeout(5.0)

    def connect(self):
        # TcpMaster otherwise opens its socket lazily on first execute();
        # pymodbus's connect() is synchronous, so mimic that here.
        self._master.open()
        return True

    def close(self):
        self._master.close()

    def read_holding_registers(self, address, count=1, unit=1):
        _spt_r2 = self._execute(
            _mtk_defines.READ_HOLDING_REGISTERS, address, unit=unit, count=count
        )
        return _spt_r2

    def write_register(self, address, value, unit=1):
        _spt_r3 = self._execute(
            _mtk_defines.WRITE_SINGLE_REGISTER, address, unit=unit, output_value=value
        )
        return _spt_r3

    def _execute(self, function_code, address, unit=1, count=None, output_value=None):
        try:
            result = self._master.execute(unit, function_code, address, count) if not (output_value is not None) else self._master.execute(
                unit, function_code, address, output_value=output_value
            )
            _spt_r4 = _ModbusTkResponse(result)
            return _spt_r4
        except _mtk_exceptions.ModbusError:
            _spt_r5 = _ModbusTkResponse(None, error=True)
            # A Modbus-level exception response (e.g. illegal address) from
            # the slave; pymodbus surfaces this same condition as a response
            # object with isError() True rather than raising, so mirror that.
            return _spt_r5


def _axiom_modbus_tcp_client_wrapper(host, port=502):
    """Build a modbus_tk-backed stand-in for pymodbus's ModbusTcpClient.

    pymodbus's ModbusTcpClient(host, port=port) plus .connect() gives an
    object with read_holding_registers()/write_register() methods that
    return a response with .isError()/.registers. modbus-tk instead
    requires constructing a modbus_tk.modbus_tcp.TcpMaster and calling
    execute() with cstruct function-code constants and a positional slave
    id ('unit' in pymodbus terms). This wrapper hides that difference
    behind _ModbusTkClientFacade so downstream code (_read1, write_valve)
    needs no changes.
    """
    client = _ModbusTkClientFacade(host, port)
    client.connect()
    return client


def _default_client(host: str, port: int = 502):
    _spt_r6 = _axiom_modbus_tcp_client_wrapper(host, port)

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
