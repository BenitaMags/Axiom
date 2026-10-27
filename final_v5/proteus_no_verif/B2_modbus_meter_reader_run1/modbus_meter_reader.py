"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomModbusTkResult:
    """Shim mimicking pymodbus's read_holding_registers() response object.

    pymodbus returns a response object exposing `.isError()` (bool) and
    `.registers` (list of ints). modbus-tk's `master.execute(...)` instead
    either returns a plain tuple of register values on success or raises a
    `modbus_tk.exceptions.ModbusError` (or other exception) on failure. This
    shim normalizes the modbus-tk call into the pymodbus-shaped object so the
    existing call-site logic (`result.isError()` / `result.registers`) keeps
    working unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers
        self._error = error

    def isError(self) -> bool:
        _spt_r1 = self._error
        return _spt_r1


class _AxiomModbusTkClient:
    """Adapter exposing a pymodbus-like client interface over modbus-tk.

    pymodbus's `ModbusTcpClient` exposes `.connect()` to open the socket and
    `.read_holding_registers(address, count, unit=...)` returning a response
    object with `.isError()`/`.registers`. modbus-tk instead uses a
    `TcpMaster` that is opened with `.open()` and read via
    `master.execute(slave, cst.READ_HOLDING_REGISTERS, address, count)`,
    which returns a raw tuple of values or raises `ModbusError` on failure.
    This wrapper opens the TcpMaster eagerly (mirroring pymodbus's explicit
    connect step) and translates `read_holding_registers` calls into
    `execute()` calls, catching only `ModbusError` to translate it into the
    pymodbus-style `isError()` result rather than letting an unrelated
    exception type leak through the previously-uniform interface.
    """

    def __init__(self, host: str, port: int = 502):
        import modbus_tk.modbus_tcp as modbus_tcp

        self._master = modbus_tcp.TcpMaster(host=host, port=port)
        self._master.open()

    def connect(self):
        # modbus-tk's TcpMaster is already opened in __init__; pymodbus's
        # .connect() is a no-op here since the socket is already live.
        return True

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst
        from modbus_tk.exceptions import ModbusError

        try:
            values = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except ModbusError:
            _spt_r2 = _AxiomModbusTkResult(registers=None, error=True)
            return _spt_r2
        _spt_r3 = _AxiomModbusTkResult(registers=list(values), error=False)
        return _spt_r3


def _axiom_modbus_tk_client_wrapper(host: str, port: int = 502):
    """Construct a modbus-tk based client that mimics pymodbus's ModbusTcpClient.

    pymodbus's `_default_client` built a `ModbusTcpClient(host, port=port)`
    and called `.connect()`, returning an object with
    `.read_holding_registers(address, count, unit=...)` yielding a response
    with `.isError()`/`.registers`. modbus-tk has no equivalent single-call
    client; it requires constructing a `TcpMaster`, calling `.open()`, and
    reading via `.execute()` which returns raw tuples or raises. This
    function returns `_AxiomModbusTkClient`, which performs that
    construction/open step and exposes the same `connect()` /
    `read_holding_registers()` surface so callers need no further changes.
    """
    client = _AxiomModbusTkClient(host, port=port)
    client.connect()
    return client


def _default_client(host: str, port: int = 502):
    _spt_r4 = _axiom_modbus_tk_client_wrapper(host, port=port)
    return _spt_r4


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
