import modbus_tk
"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


class _AxiomModbusTkResponse:
    """Lightweight response shim mimicking pymodbus's response objects.

    pymodbus calls return an object exposing `.isError()` and `.registers`.
    modbus-tk's `master.execute(...)` either returns a tuple of register
    values directly on success, or raises an exception (e.g. a
    `modbus_tk.exceptions.ModbusError` or a socket error) on failure. This
    shim normalizes both outcomes back into the pymodbus-style interface so
    downstream code (`ModbusIO._read1`, `ModbusIO.write_valve`) does not
    need to change its control flow.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _AxiomModbusTkClientShim:
    """Adapts a modbus_tk TcpMaster to the pymodbus client call signature.

    pymodbus clients expose `connect()`, `read_holding_registers(address,
    count, unit=...)`, and `write_register(address, value, unit=...)`, each
    returning a response object with `.isError()`/`.registers`. modbus-tk's
    `TcpMaster` instead exposes `open()`/`close()` and a single `execute(
    slave, function_code, address, quantity_or_value, ...)` method that
    raises an exception on failure and returns a tuple of raw register
    values on success. This shim re-implements the pymodbus-style methods
    on top of modbus_tk's `execute()` so existing call sites keep working
    unchanged.
    """

    def __init__(self, master):
        self._master = master

    def connect(self):
        # modbus_tk masters are opened explicitly by the wrapper factory
        # that constructs them, so this is a no-op kept only to preserve
        # the pymodbus-style call chain (`client.connect()`).
        return True

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst

        try:
            result = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
            return _AxiomModbusTkResponse(registers=list(result), error=False)
        except Exception:
            return _AxiomModbusTkResponse(registers=[], error=True)

    def write_register(self, address, value, unit=1):

        try:
            result = self._master.execute(
                unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
            return _AxiomModbusTkResponse(registers=list(result), error=False)
        except Exception:
            return _AxiomModbusTkResponse(registers=[], error=True)


def _axiom_modbus_tk_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's `ModbusTcpClient(host, port=port)` construction.

    pymodbus returns a client whose `.connect()`, `.read_holding_registers`,
    and `.write_register` calls yield response objects with `.isError()`
    and `.registers`. modbus-tk instead requires building a `TcpMaster`,
    calling `.open()` explicitly, and issuing requests via `.execute(unit,
    function_code, address, ...)`, which raises exceptions on failure
    instead of returning an error-flagged object. This wrapper constructs
    and opens a `TcpMaster`, then wraps it in `_AxiomModbusTkClientShim` so
    the rest of the module can keep using the pymodbus-style call chain
    (`connect()` / `read_holding_registers(...)` / `write_register(...)`
    with `.isError()` / `.registers`).
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.open()
    return _AxiomModbusTkClientShim(master)


def _default_client(host: str, port: int = 502):

    del ModbusTcpClient  # unused: construction now goes through the modbus-tk wrapper below
    client = _axiom_modbus_tk_client_wrapper(host, port=port)
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
