"""modbus_bridge -- adapts modbus-tk's TcpMaster to a pymodbus-shaped API.

pymodbus's `ModbusTcpClient` exposes `.connect()`/`.close()` lifecycle methods
and per-call methods `read_holding_registers(address, count, unit=...)` /
`write_register(address, value, unit=...)` that return response objects with
`.isError()` and `.registers`.

modbus-tk's `TcpMaster` instead exposes `.open()`/`.close()` and a single
`execute(unit, function_code, address, count_or_value)` call that returns a
raw tuple of register values (reads) or raises an exception on failure.

This module bridges the two so callers can keep using the pymodbus-style
`.isError()` / `.registers` interface unchanged.
"""


class _AxiomModbusResponse:
    """Lightweight stand-in for pymodbus's read/write response objects.

    Wraps a successful tuple result (or records an error) so downstream code
    can keep using the pymodbus-style `.isError()` / `.registers` interface
    unchanged.
    """

    def __init__(self, values=None, error: bool = False):
        self._error = error
        self.registers = list(values) if values is not None else []

    def isError(self) -> bool:
        return self._error


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge modbus-tk's TcpMaster to the pymodbus ModbusTcpClient API.

    Instantiates a TcpMaster, opens the connection to mimic pymodbus's
    connect-on-construct behavior, and attaches shim methods
    `read_holding_registers` and `write_register` that call `master.execute`
    with the appropriate function code and wrap the raw tuple result into an
    `_AxiomModbusResponse`. Exceptions from `execute` are caught and reported
    via `isError()` returning True, matching the pymodbus-style contract
    expected by callers.
    """
    import modbus_tk.defines as cst
    from modbus_tk import modbus_tcp

    master = modbus_tcp.TcpMaster(host, port=port)
    master.open()

    def read_holding_registers(address, count, unit=1):
        try:
            values = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
            return _AxiomModbusResponse(values=values)
        except Exception:
            return _AxiomModbusResponse(error=True)

    def write_register(address, value, unit=1):
        try:
            values = master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, value)
            return _AxiomModbusResponse(values=values)
        except Exception:
            return _AxiomModbusResponse(error=True)

    master.read_holding_registers = read_holding_registers
    master.write_register = write_register
    return master


def _default_client(host: str, port: int = 502):
    return _axiom_modbus_client_wrapper(host, port=port)
