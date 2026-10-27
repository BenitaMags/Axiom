"""modbus_adapter -- glue between modbus-tk's API and pymodbus's contract.

Extracted from transport.py so the translation logic (exception handling,
response-shape mirroring) lives in one place, independent of the ModbusIO
class that consumes it.
"""


class ModbusResponse:
    """Mimics pymodbus's response object shape.

    Carries ``.registers`` (list of values read/written) and an
    ``isError()`` method reporting True on failure.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class ModbusTkClientAdapter:
    """Adapts modbus-tk's ``TcpMaster`` to pymodbus's client API."""

    def __init__(self, host, port):
        import modbus_tk.modbus_tcp as modbus_tcp

        self._master = modbus_tcp.TcpMaster(host, port)

    def connect(self):
        """Mirror pymodbus's connect(): True on success, False on failure."""
        from modbus_tk.exceptions import ModbusError

        try:
            self._master.open()
        except (ModbusError, OSError):
            return False
        return True

    def read_holding_registers(self, address, count, unit=1):
        """Mirror pymodbus's read_holding_registers() response shape."""
        import modbus_tk.defines as cst
        from modbus_tk.exceptions import ModbusError

        try:
            result = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except (ModbusError, OSError):
            return ModbusResponse(error=True)
        return ModbusResponse(registers=list(result))

    def write_register(self, address, value, unit=1):
        """Mirror pymodbus's write_register() response shape."""
        import modbus_tk.defines as cst
        from modbus_tk.exceptions import ModbusError

        try:
            result = self._master.execute(
                unit,
                cst.WRITE_SINGLE_REGISTER,
                address,
                output_value=value,
            )
        except (ModbusError, OSError):
            return ModbusResponse(error=True)
        return ModbusResponse(registers=list(result) if result else [value])


def build_modbus_tk_client(host: str, port: int = 502):
    """Construct and connect a modbus-tk backed client with pymodbus's API."""
    client = ModbusTkClientAdapter(host, port)
    client.connect()
    return client
