"""Helper module providing a modbus-tk backed client adapter exposing the
pymodbus-style API (`connect`, `close`, `read_holding_registers`,
`write_register`) expected by `transport.ModbusIO`.
"""


class _Response:
    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _ModbusTkClientAdapter:
    def __init__(self, host, port):
        import modbus_tk.modbus_tcp as modbus_tcp

        self._master = modbus_tcp.TcpMaster(host=host, port=port)
        self._master.set_timeout(5.0)

    def connect(self):
        import modbus_tk.exceptions as mtk_exceptions

        try:
            self._master.open()
            return True
        except (mtk_exceptions.ModbusError, OSError):
            return False

    def close(self):
        self._master.close()

    def read_holding_registers(self, address, count=1, unit=1):
        import modbus_tk.defines as cst
        import modbus_tk.exceptions as mtk_exceptions

        try:
            result = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except (mtk_exceptions.ModbusError, OSError):
            return _Response(error=True)
        return _Response(registers=list(result))

    def write_register(self, address, value, unit=1):
        import modbus_tk.defines as cst
        import modbus_tk.exceptions as mtk_exceptions

        try:
            result = self._master.execute(
                unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
        except (mtk_exceptions.ModbusError, OSError):
            return _Response(error=True)
        registers = list(result) if result is not None else [value]
        return _Response(registers=registers)


def build_modbus_tk_client(host: str, port: int = 502):
    return _ModbusTkClientAdapter(host, port)
