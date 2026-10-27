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
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


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
        return self._execute(
            _mtk_defines.READ_HOLDING_REGISTERS, address, unit=unit, count=count
        )

    def write_register(self, address, value, unit=1):
        return self._execute(
            _mtk_defines.WRITE_SINGLE_REGISTER, address, unit=unit, output_value=value
        )

    def _execute(self, function_code, address, unit=1, count=None, output_value=None):
        try:
            if output_value is not None:
                result = self._master.execute(
                    unit, function_code, address, output_value=output_value
                )
            else:
                result = self._master.execute(unit, function_code, address, count)
            return _ModbusTkResponse(result)
        except _mtk_exceptions.ModbusError:
            # A Modbus-level exception response (e.g. illegal address) from
            # the slave; pymodbus surfaces this same condition as a response
            # object with isError() True rather than raising, so mirror that.
            return _ModbusTkResponse(None, error=True)


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

    return _axiom_modbus_tcp_client_wrapper(host, port)


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
