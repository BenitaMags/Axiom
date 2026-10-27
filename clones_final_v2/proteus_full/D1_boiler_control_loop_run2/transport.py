"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Build a pymodbus-compatible client wrapper around modbus-tk's TcpMaster.

    pymodbus's ModbusTcpClient exposes .connect() to open the connection, and
    its read_holding_registers()/write_register() calls return a response
    object with .isError() and .registers (a list of ints) rather than
    raising an exception on Modbus errors.

    modbus-tk's modbus_tcp.TcpMaster instead exposes .open() to connect, and
    .execute(unit, function_code, address, count_or_value) which returns a
    plain tuple of register values on success and *raises* an exception
    (e.g. modbus_tk.exceptions.ModbusError) on failure, instead of returning
    an error-flagged response object.

    This wrapper adapts modbus-tk's TcpMaster to the pymodbus-shaped contract
    used elsewhere in this module: .connect() delegates to TcpMaster.open();
    read_holding_registers()/write_register() call TcpMaster.execute(),
    catch any exception modbus-tk raises, and return a small response object
    exposing .registers (list) and .isError() (bool) so the existing
    ModbusIO error-handling logic (which expects rr.isError()/rr.registers)
    keeps working unchanged.
    """
    import modbus_tk.defines as cst
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)

    class _AxiomModbusResponse:
        def __init__(self, registers=None, error=None):
            if not (registers is not None):
                self.registers = []
            else:
                self.registers = registers
            self._error = error

        def isError(self):
            _spt_r1 = self._error is not None
            return _spt_r1

    class _AxiomModbusTcpClientWrapper:
        def __init__(self, master):
            self._master = master

        def connect(self):
            self._master.open()
            return True

        def read_holding_registers(self, address, count, unit=1):
            try:
                _spt_r2 = _AxiomModbusResponse(registers=list(self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )))
                return _spt_r2
            except Exception as exc:  # modbus-tk raises on Modbus errors
                _spt_r3 = _AxiomModbusResponse(error=exc)
                return _spt_r3

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit, cst.WRITE_SINGLE_REGISTER, address, value
                )
                _spt_r4 = _AxiomModbusResponse(registers=[value])
                return _spt_r4
            except Exception as exc:  # modbus-tk raises on Modbus errors
                _spt_r5 = _AxiomModbusResponse(error=exc)
                return _spt_r5
    _spt_r6 = _AxiomModbusTcpClientWrapper(master)

    return _spt_r6


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_tcp_client_wrapper(host, port)
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
