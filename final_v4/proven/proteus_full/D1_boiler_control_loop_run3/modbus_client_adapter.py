"""Helper module providing the modbus-tk-backed pymodbus-shaped client.

Kept separate from transport.py so the modbus-tk import stays deferred:
this module is only imported (and its heavy import only executed) inside
the factory functions in transport.py, never at module load time.
"""


class ModbusResponse:
    """pymodbus-shaped response: isError() + registers, filled in by the adapter."""

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


def _transport_errors():
    import socket

    from modbus_tk.exceptions import ModbusError, ModbusInvalidResponseError

    return (ModbusError, ModbusInvalidResponseError, socket.error, socket.timeout)


class ModbusTkClient:
    """Thin wrapper around modbus_tk.modbus_tcp.TcpMaster exposing the
    subset of pymodbus's ModbusTcpClient API that ModbusIO relies on."""

    def __init__(self, host, port):
        import modbus_tk.modbus_tcp as modbus_tcp

        self._master = modbus_tcp.TcpMaster(host=host, port=port)
        self._master.set_timeout(5.0)
        self._errors = _transport_errors()

    def connect(self):
        # pymodbus's connect() returns True/False rather than raising;
        # mirror that by catching connection-level failures here.
        try:
            self._master.open()
            return True
        except self._errors:
            return False

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst

        try:
            values = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
            return ModbusResponse(registers=list(values))
        except self._errors:
            return ModbusResponse(error=True)

    def write_register(self, address, value, unit=1):
        import modbus_tk.defines as cst

        try:
            result = self._master.execute(
                unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
            return ModbusResponse(registers=list(result) if result else [value])
        except self._errors:
            return ModbusResponse(error=True)

    def close(self):
        self._master.close()


def build_modbus_tk_client(host: str, port: int = 502):
    return ModbusTkClient(host, port)
