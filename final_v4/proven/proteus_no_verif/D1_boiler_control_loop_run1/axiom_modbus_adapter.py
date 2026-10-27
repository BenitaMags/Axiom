"""Glue layer adapting modbus_tk's TcpMaster to a pymodbus-shaped client API.

Kept separate from transport.py so the adaptation logic between the
modbus-tk exception-based error model and the pymodbus response-based
error model can be reused or tested independently.
"""


class AxiomModbusResponse:
    """Fake pymodbus-style response wrapping a modbus-tk execute() result.

    pymodbus's read_holding_registers()/write_register() return a response
    object exposing .isError() and (for reads) .registers. modbus-tk's
    master.execute() instead either returns a tuple of register values (for
    reads) or a tuple echoing the written address/value (for writes), and
    raises a ModbusError subclass on failure instead of returning an error
    response. This wrapper captures either the successful result or the
    raised exception so call sites can keep using .isError()/.registers
    unchanged.
    """

    def __init__(self, registers=None, error=None):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


class AxiomModbusClientAdapter:
    """Adapts modbus_tk.modbus_tcp.TcpMaster to the pymodbus client API used
    by this module (connect(), read_holding_registers(), write_register()).
    """

    def __init__(self, master, default_unit: int = 1):
        self._master = master
        self._default_unit = default_unit

    def connect(self):
        # modbus-tk's TcpMaster opens its connection at construction time,
        # so there is nothing left to do here; kept for API compatibility
        # with pymodbus's explicit .connect() call.
        return True

    def _resolve_unit(self, unit):
        return unit if unit is not None else self._default_unit

    def read_holding_registers(self, address, count, unit=None):
        import modbus_tk.defines as cst
        from modbus_tk.exceptions import ModbusError

        slave_id = self._resolve_unit(unit)
        try:
            result = self._master.execute(
                slave_id, cst.READ_HOLDING_REGISTERS, address, count
            )
        except ModbusError as exc:
            return AxiomModbusResponse(error=exc)
        return AxiomModbusResponse(registers=result)

    def write_register(self, address, value, unit=None):
        import modbus_tk.defines as cst
        from modbus_tk.exceptions import ModbusError

        slave_id = self._resolve_unit(unit)
        try:
            result = self._master.execute(
                slave_id,
                cst.WRITE_SINGLE_REGISTER,
                address,
                output_value=value,
            )
        except ModbusError as exc:
            return AxiomModbusResponse(error=exc)
        return AxiomModbusResponse(registers=result)


def axiom_modbus_client_wrapper(host: str, port: int = 502, unit: int = 1):
    """Build a modbus-tk TcpMaster and wrap it to look like a pymodbus client.

    pymodbus's ModbusTcpClient(host, port=port) followed by .connect()
    returns an object whose read_holding_registers()/write_register() calls
    give back a response with .isError()/.registers, and raises nothing on a
    protocol error. modbus-tk's TcpMaster connects implicitly on
    construction and its master.execute() calls return plain tuples on
    success but raise a ModbusError subclass on failure. This wrapper
    constructs the TcpMaster and returns an AxiomModbusClientAdapter that
    re-exposes the pymodbus-shaped methods, translating modbus-tk's
    exceptions into the .isError()/.registers response shape the call sites
    already expect.
    """
    from modbus_tk.modbus_tcp import TcpMaster  # lazy import for testability

    master = TcpMaster(host=host, port=port)
    master.set_timeout(5.0)
    return AxiomModbusClientAdapter(master, default_unit=unit)
