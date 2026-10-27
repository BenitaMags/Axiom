"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


class _AxiomModbusResult:
    """Lightweight stand-in for a pymodbus response object.

    pymodbus read/write calls return a response object exposing `.isError()`
    and (for reads) `.registers`. modbus-tk's `master.execute(...)` instead
    either returns a tuple of register values directly, or raises an
    exception (subclass of `modbus_tk.exceptions.ModbusError` or a socket
    error) on failure. This tiny object re-creates the `.isError()` /
    `.registers` surface so existing call sites (`ModbusIO._read1` and
    `ModbusIO.write_valve`) do not need to be restructured around
    try/except -- the wrapper below does that restructuring once, here.
    """

    def __init__(self, registers=None, error: bool = False):
        if not (registers is not None):
            self.registers = []
        else:
            self.registers = registers
        self._error = error

    def isError(self) -> bool:
        _spt_r1 = self._error
        return _spt_r1


class _AxiomModbusTcpClientAdapter:
    """Adapts a modbus_tk TcpMaster to the pymodbus client method surface.

    pymodbus's `ModbusTcpClient` exposes `read_holding_registers(address,
    count, unit=...)` and `write_register(address, value, unit=...)`, each
    returning a response object with `.isError()`/`.registers`. modbus-tk's
    `TcpMaster` instead exposes a single `execute(slave, function_code,
    address, quantity_of_x=None, output_value=None, ...)` call that returns
    a tuple of values on success and raises an exception on failure. This
    adapter translates the pymodbus-shaped calls into modbus_tk `execute()`
    calls, catches modbus_tk's exceptions, and returns `_AxiomModbusResult`
    objects so `ModbusIO._read1` / `ModbusIO.write_valve` keep working
    unmodified.
    """

    def __init__(self, master):
        self._master = master

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst
        import modbus_tk.exceptions as mtk_exceptions

        try:
            _spt_r2 = _AxiomModbusResult(registers=list(self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )), error=False)
            return _spt_r2
        except (mtk_exceptions.ModbusError, IOError, ValueError):
            _spt_r3 = _AxiomModbusResult(error=True)
            return _spt_r3

    def write_register(self, address, value, unit=1):
        import modbus_tk.defines as cst
        import modbus_tk.exceptions as mtk_exceptions

        try:
            self._master.execute(
                unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
            _spt_r4 = _AxiomModbusResult(error=False)
            return _spt_r4
        except (mtk_exceptions.ModbusError, IOError, ValueError):
            _spt_r5 = _AxiomModbusResult(error=True)
            return _spt_r5


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Build a modbus_tk-backed replacement for pymodbus's ModbusTcpClient.

    pymodbus usage was `ModbusTcpClient(host, port=port)` followed by
    `.connect()`, then reads/writes via `.read_holding_registers(...)` /
    `.write_register(...)` returning objects with `.isError()`/`.registers`.
    modbus-tk instead uses `modbus_tk.modbus_tcp.TcpMaster(host, port)` with
    an `.open()` lifecycle call, and a generic `.execute(...)` method that
    raises on error instead of returning an error-flagged object. This
    wrapper constructs the TcpMaster, opens the connection, and wraps it in
    `_AxiomModbusTcpClientAdapter` so downstream code can keep calling the
    pymodbus-shaped API without any further changes.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.open()
    _spt_r6 = _AxiomModbusTcpClientAdapter(master)
    return _spt_r6


def _default_client(host: str, port: int = 502):
    _spt_r7 = _axiom_modbus_tcp_client_wrapper(host, port=port)
    return _spt_r7



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
        _spt_r8 = rr.registers[0]
        return _spt_r8

    def read_temperature(self, address: int = 0x0000) -> float:
        _spt_r9 = self._read1(address) / 10.0
        return _spt_r9  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        _spt_r10 = self._read1(address) / 100.0
        return _spt_r10  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
