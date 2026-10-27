"""modbustk_compat -- glue between pymodbus-shaped call sites and modbus-tk.

Extracted from transport.py: this module knows how to translate modbus-tk's
tuple-or-exception ``TcpMaster.execute`` API into the ``.registers`` /
``.isError()`` response shape that pymodbus's ``ModbusTcpClient`` exposes.
"""


class ModbusTkResult:
    """Small stand-in for pymodbus's response object.

    pymodbus's ``read_holding_registers``/``write_register`` return an object
    exposing ``.registers`` (a list of ints, for reads) and ``.isError()``
    (a bool). modbus-tk's ``TcpMaster.execute`` instead returns a plain tuple
    of register values on success and *raises* an exception (e.g.
    ``ModbusError``/``ModbusInvalidResponseError``) on failure. This class
    re-wraps modbus-tk's result/exception into the same ``.registers`` /
    ``.isError()`` shape the call sites already expect, so ModbusIO does not
    need to change.
    """

    def __init__(self, registers=None, error=False):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _ModbusTkClientFacade:
    def __init__(self, host, port):
        import modbus_tk.modbus_tcp as modbus_tcp

        self._master = modbus_tcp.TcpMaster(host, port)

    def connect(self):
        self._master.open()

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst
        from modbus_tk.exceptions import ModbusError, ModbusInvalidResponseError

        try:
            result = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except (ModbusError, ModbusInvalidResponseError):
            return ModbusTkResult(registers=[], error=True)
        return ModbusTkResult(registers=list(result), error=False)

    def write_register(self, address, value, unit=1):
        import modbus_tk.defines as cst
        from modbus_tk.exceptions import ModbusError, ModbusInvalidResponseError

        try:
            self._master.execute(
                unit,
                cst.WRITE_SINGLE_REGISTER,
                address,
                output_value=value,
            )
        except (ModbusError, ModbusInvalidResponseError):
            return ModbusTkResult(registers=[], error=True)
        return ModbusTkResult(registers=[], error=False)


def build_modbustk_client(host: str, port: int = 502):
    """Construct a modbus-tk backed facade mimicking pymodbus's ModbusTcpClient."""
    return _ModbusTkClientFacade(host, port)
