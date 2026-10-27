"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_factory(host: str, port: int = 502):
    """Bridge pymodbus-style client construction to modbus-tk.

    pymodbus's `ModbusTcpClient(host, port=port)` yields an object with an
    explicit `.connect()` method, and its `read_holding_registers` /
    `write_register` calls return a response object exposing `.isError()`
    and (on reads) a `.registers` list.

    modbus-tk instead exposes a `modbus_tk.modbus_tcp.TcpMaster(host, port)`
    that must be `.open()`ed before use, and whose `.execute(slave, func,
    address, count/value)` calls either return a plain tuple of register
    values on success or raise a `modbus_tk.exceptions.ModbusError` on
    failure -- there is no `.isError()`/`.registers` protocol.

    This wrapper constructs and opens the modbus-tk master, then returns a
    small adapter object that reproduces the pymodbus-shaped API
    (`connect()`, `read_holding_registers(address, count, unit=...)`,
    `write_register(address, value, unit=...)`) by calling
    `master.execute(...)` internally, catching `ModbusError`, and wrapping
    the outcome in a tiny result object with `.registers` and `.isError()`
    so that existing call sites (`rr.isError()`, `rr.registers[0]`, etc.)
    keep working unmodified.
    """
    import modbus_tk.defines as cst
    from modbus_tk.exceptions import ModbusError
    from modbus_tk.modbus_tcp import TcpMaster

    master = TcpMaster(host, port)
    master.open()

    class _Result:
        def __init__(self, registers=None, error=False):
            self.registers = registers if registers is not None else []
            self._error = error

        def isError(self) -> bool:
            return self._error

    class _ClientAdapter:
        def connect(self):
            # modbus-tk master is already opened during construction;
            # nothing further is required, but keep the method for
            # lifecycle parity with the old pymodbus-based call sites.
            return True

        def read_holding_registers(self, address, count, unit=1):
            try:
                result = master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
                return _Result(registers=list(result), error=False)
            except ModbusError:
                return _Result(error=True)

        def write_register(self, address, value, unit=1):
            try:
                master.execute(
                    unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
                )
                return _Result(registers=[value], error=False)
            except ModbusError:
                return _Result(error=True)

    return _ClientAdapter()


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_client_factory(host, port=port)
    client.connect()
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
