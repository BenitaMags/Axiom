"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Bridge modbus-tk's TcpMaster API to the pymodbus-style client shape.

    pymodbus's ModbusTcpClient.read_holding_registers()/write_register()
    return response objects exposing `.isError()` (bool) and, for reads,
    `.registers` (a list of ints). modbus-tk's TcpMaster has no such
    response object: `TcpMaster.execute()` returns a plain tuple of
    register values on success and raises a modbus_tk exception
    (modbus_tk.exceptions.ModbusError or a subclass) on failure -- there is
    no return-based error signal at all.

    This wrapper constructs and opens a modbus_tk TcpMaster for
    (host, port) and returns a thin adapter exposing connect(),
    read_holding_registers(address, count, unit=...) and
    write_register(address, value, unit=...), each implemented via
    master.execute(unit, <function code>, address, count_or_value). The
    adapter catches only modbus_tk's own ModbusError (never a bare
    Exception) and translates it into a pymodbus-style response object
    whose `.isError()` returns True, so the existing `_read1`/`write_valve`
    callers -- written against pymodbus's return-value error contract --
    keep working unchanged. Any other exception (e.g. programming errors)
    propagates normally, matching pymodbus's behavior of not swallowing
    unexpected failures.
    """
    import modbus_tk.defines as cst
    import modbus_tk.modbus_tcp as modbus_tcp
    from modbus_tk.exceptions import ModbusError

    class _Response:
        def __init__(self, registers=None, error=False):
            self.registers = registers
            self._error = error

        def isError(self):
            return self._error

    class _Adapter:
        def __init__(self, master):
            self._master = master

        def connect(self):
            self._master.open()
            return True

        def read_holding_registers(self, address, count, unit=1):
            try:
                values = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except ModbusError:
                return _Response(error=True)
            return _Response(registers=list(values))

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit, cst.WRITE_SINGLE_REGISTER, address, value
                )
            except ModbusError:
                return _Response(error=True)
            return _Response(registers=[value])

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.set_timeout(5.0)
    return _Adapter(master)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_tcp_client_wrapper(host, port)
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
