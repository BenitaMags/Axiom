"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomModbusResult:
    """Small shim mimicking the pymodbus response object.

    Exposes `.registers` (the list/tuple of register values) and
    `.isError()` (False on success, True if the underlying modbus-tk call
    raised an exception, with the exception stored on `.exception`).
    """

    def __init__(self, registers=None, exception=None):
        self.registers = registers
        self.exception = exception

    def isError(self):
        _spt_r1 = self.exception is not None
        return _spt_r1


class _AxiomModbusTcpClientAdapter:
    """Adapter exposing a pymodbus-like interface backed by modbus_tk.

    pymodbus's `ModbusTcpClient` provides `.connect()` and
    `.read_holding_registers(address, count, unit=...)`, where the latter
    returns a response object with `.registers` and `.isError()`.
    modbus-tk's `TcpMaster` instead uses `.open()` to connect and
    `.execute(unit, function_code, address, count)` to perform a request,
    returning a plain tuple of register values and raising a modbus_tk
    exception on failure. This adapter wraps a `TcpMaster` so existing
    call sites written against the pymodbus shape keep working unmodified.
    """

    def __init__(self, master):
        self._master = master

    def connect(self):
        _spt_r2 = self._master.open()
        return _spt_r2

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst

        try:
            registers = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
            _spt_r3 = _AxiomModbusResult(registers=list(registers))
            return _spt_r3
        except Exception as exc:  # modbus_tk raises on error instead of isError()
            _spt_r4 = _AxiomModbusResult(exception=exc)
            return _spt_r4


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Build a modbus-tk based client that mimics pymodbus's ModbusTcpClient.

    pymodbus's `ModbusTcpClient(host, port=port)` returns an object with
    `.connect()` and `.read_holding_registers(...)` returning a response
    object exposing `.registers`/`.isError()`. modbus-tk instead requires
    constructing a `TcpMaster(host, port)` and calling `.open()` /
    `.execute(unit, function_code, address, count)`, which returns a raw
    tuple and raises exceptions on error. This wrapper constructs the
    `TcpMaster` and returns an adapter that presents the pymodbus-style
    API so call sites don't need to change.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host, port)
    _spt_r5 = _AxiomModbusTcpClientAdapter(master)
    return _spt_r5


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_tcp_client_wrapper(host, port=port)
    client.connect()
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if not (client is not None):
            self.client = _default_client(host)
        else:
            self.client = client
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self.client.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
