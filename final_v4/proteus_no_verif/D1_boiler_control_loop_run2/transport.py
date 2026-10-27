"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _modbustk_client_adapter(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient surface onto modbus-tk's TcpMaster.

    pymodbus's ModbusTcpClient exposes .connect() plus
    .read_holding_registers(address, count, unit=...) and
    .write_register(address, value, unit=...) calls that return a response
    object with .isError() and (for reads) .registers -- failures are
    reported via isError() rather than by raising.

    modbus-tk's TcpMaster instead exposes .open()/.close() and a single
    .execute(slave_id, function_code, address, count=..., output_value=...)
    call that returns a raw tuple of register values on success and raises a
    modbus_tk.exceptions.ModbusError (or a subclass) on protocol failure.

    This adapter builds a small facade that mimics the pymodbus surface used
    by ModbusIO: .connect() opens the TcpMaster connection, and
    .read_holding_registers()/.write_register() call TcpMaster.execute() and
    translate a raised ModbusError into a response object whose isError()
    returns True (matching what pymodbus itself returns for the same class
    of failure), or wrap the raw tuple of registers into a response exposing
    .registers, so existing call sites keep working unchanged.
    """
    import modbus_tk.defines as cst
    import modbus_tk.exceptions as mtk_exc
    from modbus_tk import modbus_tcp

    class _Response:
        def __init__(self, registers=None, error=False):
            if not (registers is not None):
                self.registers = []
            else:
                self.registers = registers
            self._error = error

        def isError(self):
            _spt_r1 = self._error
            return _spt_r1

    class _TcpMasterFacade:
        def __init__(self, host, port):
            self._master = modbus_tcp.TcpMaster(host=host, port=port)

        def connect(self):
            self._master.open()

        def read_holding_registers(self, address, count, unit=1):
            try:
                result = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except mtk_exc.ModbusError:
                _spt_r2 = _Response(error=True)
                # modbus-tk raises on a protocol-level failure where
                # pymodbus instead returns a response with isError() True;
                # reproduce that same status here rather than inventing one.
                return _spt_r2
            _spt_r3 = _Response(registers=list(result))
            return _spt_r3

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
            except mtk_exc.ModbusError:
                _spt_r4 = _Response(error=True)
                return _spt_r4
            _spt_r5 = _Response(error=False)
            return _spt_r5
    _spt_r6 = _TcpMasterFacade(host, port)

    return _spt_r6


def _default_client(host: str, port: int = 502):

    client = _modbustk_client_adapter(host, port=port)
    client.connect()
    return client


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
