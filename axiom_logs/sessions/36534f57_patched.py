"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


class _AxiomModbusResponse:
    """Lightweight stand-in for pymodbus's read/write response objects.

    pymodbus's read/write calls return an object exposing `.isError()` and, for
    reads, `.registers` (a list of ints). modbus-tk's `master.execute(...)`
    instead either returns a plain tuple of register values (on success) or
    raises an exception (on failure). This wraps a successful tuple result so
    downstream code (`_read1`, `write_valve`) can keep using the pymodbus-style
    `.isError()` / `.registers` interface unchanged.
    """

    def __init__(self, values=None, error: bool = False):
        self._error = error
        self.registers = list(values) if values is not None else []

    def isError(self) -> bool:
        return self._error


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge modbus-tk's TcpMaster to the pymodbus ModbusTcpClient API.

    pymodbus's `ModbusTcpClient(host, port=port)` exposes `.connect()`/`.close()`
    lifecycle methods and per-call methods `read_holding_registers(address,
    count, unit=...)` / `write_register(address, value, unit=...)` that return
    response objects with `.isError()` and `.registers`.

    modbus-tk's `modbus_tcp.TcpMaster(host, port=port)` instead exposes
    `.open()`/`.close()` and a single `execute(unit, function_code, address,
    count_or_value)` call that returns a raw tuple of register values (reads)
    or raises `modbus_tk.exceptions.ModbusError` (or similar) on failure.

    This wrapper instantiates a TcpMaster, opens the connection to mimic
    pymodbus's connect-on-construct behavior, and attaches shim methods
    `read_holding_registers` and `write_register` that call `master.execute`
    with the appropriate function code and wrap the raw tuple result into an
    `_AxiomModbusResponse` so `_read1`/`write_valve` need no further changes.
    Exceptions from `execute` are allowed to propagate (matching pymodbus's
    `.isError()` == True path is only used for the "we caught it ourselves"
    case; real failures still raise, same as pymodbus's `IOError` path that
    follows an `isError()` check would ultimately surface as an exception in
    the caller).
    """
    import modbus_tk.defines as cst
    from modbus_tk import modbus_tcp

    master = modbus_tcp.TcpMaster(host, port=port)
    master.open()

    def read_holding_registers(address, count, unit=1):
        try:
            values = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
            return _AxiomModbusResponse(values=values)
        except Exception:
            return _AxiomModbusResponse(error=True)

    def write_register(address, value, unit=1):
        try:
            values = master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, value)
            return _AxiomModbusResponse(values=values)
        except Exception:
            return _AxiomModbusResponse(error=True)

    master.read_holding_registers = read_holding_registers
    master.write_register = write_register
    return master


def _default_client(host: str, port: int = 502):

    return _axiom_modbus_client_wrapper(host, port=port)


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
