"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


class _AxiomModbusTkResponse:
    """Pseudo-response object mimicking pymodbus's response shape.

    pymodbus's read/write calls return a response object exposing
    `.isError()` (bool) and, for reads, a `.registers` list of the values
    read. modbus-tk's `master.execute()` instead either returns a plain
    tuple/list of register values on success, or raises a
    `modbus_tk.exceptions.ModbusError` (or subclass) when the slave replies
    with a Modbus exception code. This object lets the rest of ModbusIO
    keep using the pymodbus-shaped `.isError()` / `.registers` interface
    unchanged, regardless of which underlying path produced the result.
    """

    def __init__(self, registers=None, error=False):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _AxiomModbusTkClientAdapter:
    """Adapter exposing a pymodbus-like client API backed by modbus-tk.

    pymodbus's `ModbusTcpClient` exposes `.connect()`, and
    `.read_holding_registers(address, count, unit=...)` /
    `.write_register(address, value, unit=...)` methods that return
    response objects with `.isError()` / `.registers`. modbus-tk's
    `modbus_tk.modbus_tcp.TcpMaster` instead exposes `.open()` for
    connecting and a single `.execute(slave_id, function_code, address,
    quantity_of_x=..., output_value=...)` method that returns a plain
    tuple of register values (or raises `ModbusError` for a slave
    exception response). This adapter translates between the two shapes
    so callers written against the pymodbus API keep working unmodified.
    """

    def __init__(self, master):
        self._master = master

    def connect(self):
        self._master.open()

    def read_holding_registers(self, address, count, unit=1):
        from modbus_tk import defines as _cst
        from modbus_tk.exceptions import ModbusError

        try:
            registers = self._master.execute(
                unit, _cst.READ_HOLDING_REGISTERS, address, count
            )
        except ModbusError:
            # pymodbus reports a slave-side Modbus exception via an error
            # response object (checked with .isError()), not by raising.
            # Reproduce that same status here rather than letting the
            # exception surface with a different shape.
            return _AxiomModbusTkResponse(error=True)
        return _AxiomModbusTkResponse(registers=registers)

    def write_register(self, address, value, unit=1):
        from modbus_tk import defines as _cst
        from modbus_tk.exceptions import ModbusError

        try:
            self._master.execute(
                unit, _cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
        except ModbusError:
            # See read_holding_registers: translate a slave-side Modbus
            # exception into the same error-response shape pymodbus used,
            # instead of letting a differently-shaped exception escape.
            return _AxiomModbusTkResponse(error=True)
        return _AxiomModbusTkResponse(registers=[value])


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Build a pymodbus-shaped TCP client backed by modbus-tk.

    pymodbus's `ModbusTcpClient(host, port=port)` is instantiated and then
    connected via `.connect()`. modbus-tk's equivalent is
    `modbus_tk.modbus_tcp.TcpMaster(host=host, port=port)`, connected via
    `.open()` instead of `.connect()`, and it lacks read/write convenience
    methods entirely (everything goes through `.execute()`). This wrapper
    constructs the modbus-tk master and wraps it in
    `_AxiomModbusTkClientAdapter`, which exposes `.connect()`,
    `.read_holding_registers()` and `.write_register()` with the same
    names/signatures/return shapes ModbusIO already expects.
    """
    from modbus_tk import modbus_tcp  # lazy import for testability

    master = modbus_tcp.TcpMaster(host=host, port=port)
    return _AxiomModbusTkClientAdapter(master)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_tcp_client_wrapper(host, port=port)
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
