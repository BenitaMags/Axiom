"""Helpers that adapt modbus-tk's Master/query API onto pymodbus's shape.

Kept in a separate module so that ``transport.py`` can defer importing
``modbus_tk`` until a real (non-injected) client is actually needed.
"""


class ModbusResponse:
    """Minimal stand-in for a pymodbus response object."""

    def __init__(self, error: bool, registers=None):
        self._error = error
        self.registers = registers if registers is not None else []

    def isError(self) -> bool:
        return self._error


class ModbusTkClientAdapter:
    """Adapts a modbus-tk ``Master`` to pymodbus's client API shape."""

    def __init__(self, master, function_defines, failure_excs):
        self._master = master
        self._cst = function_defines
        self._failure_excs = failure_excs

    def connect(self):
        return self._master.open()

    def read_holding_registers(self, address, count, unit=1):
        try:
            result = self._master.execute(
                unit, self._cst.READ_HOLDING_REGISTERS, address, count
            )
        except self._failure_excs:
            return ModbusResponse(True)
        return ModbusResponse(False, list(result))

    def write_register(self, address, value, unit=1):
        try:
            self._master.execute(
                unit,
                self._cst.WRITE_SINGLE_REGISTER,
                address,
                output_value=value,
            )
        except self._failure_excs:
            return ModbusResponse(True)
        return ModbusResponse(False)


def build_modbustk_client(host: str, port: int = 502):
    """Construct a modbus-tk ``TcpMaster`` wrapped in a pymodbus-shaped adapter."""
    from modbus_tk.simulator import modbus_tcp  # lazy import for testability
    from modbus_tk import modbus as modbus_tk_modbus
    from modbus_tk import exceptions as modbus_tk_exceptions

    master = modbus_tcp.TcpMaster(host, port)
    failure_excs = (modbus_tk_exceptions.ModbusError, OSError)
    return ModbusTkClientAdapter(master, modbus_tk_modbus.defines, failure_excs)
