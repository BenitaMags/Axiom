"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_tcp_master_facade(host: str, port: int = 502, timeout: float = 5.0):
    """Bridge modbus-tk's Master/execute() API to the pymodbus-style client
    interface expected by ModbusIO.

    pymodbus's ModbusTcpClient exposes .connect()/.close() for the
    connection lifecycle, plus per-function-code methods such as
    read_holding_registers(address, count, unit=...) and
    write_register(address, value, unit=...) that each return a response
    object with .isError() and (for reads) .registers holding the decoded
    values.

    modbus-tk instead exposes a modbus_tcp.TcpMaster with .open()/.close()
    for the connection, and a single .execute(slave_id, function_code,
    address, quantity_or_value) call that returns a plain tuple of
    register values on success and raises a modbus_tk exception on
    failure -- there is no .isError()/.registers object.

    This facade wraps a TcpMaster so the rest of this module (_read1,
    write_valve) can keep using the pymodbus-shaped calling convention and
    error-flag checks (.isError() / .registers) without further changes:
    successful execute() calls are repackaged into a small response object
    exposing .registers, and exceptions raised by execute() are caught and
    repackaged into a response object whose .isError() returns True.
    """
    import modbus_tk.defines as cst
    from modbus_tk import modbus_tcp

    class _Response:
        def __init__(self, registers=None, error=None):
            self.registers = registers
            self._error = error

        def isError(self):
            return self._error is not None

    class _MasterFacade:
        def __init__(self, host, port, timeout):
            self._master = modbus_tcp.TcpMaster(host=host, port=port)
            self._master.set_timeout(timeout)

        def connect(self):
            self._master.open()
            return True

        def close(self):
            self._master.close()

        def read_holding_registers(self, address, count, unit=1):
            try:
                values = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
                return _Response(registers=list(values))
            except Exception as exc:  # modbus_tk raises various exceptions on failure
                return _Response(error=exc)

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
                )
                return _Response(registers=[value])
            except Exception as exc:  # modbus_tk raises various exceptions on failure
                return _Response(error=exc)

    facade = _MasterFacade(host, port, timeout)
    facade.connect()
    return facade


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_tcp_master_facade(host, port=port)
    return client


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
