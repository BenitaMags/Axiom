"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _axiom_modbus_client_wrapper:
    """Bridge between pymodbus's ModbusTcpClient API and modbus-tk's TcpMaster API.

    pymodbus's ModbusTcpClient is constructed as ModbusTcpClient(host, port=port)
    and exposes a `.connect()` method that opens the underlying TCP socket.

    modbus-tk instead exposes `modbus_tk.modbus_tcp.TcpMaster(host, port)`, whose
    connection is established by calling `.open()` (there is no `.connect()`
    method at all).

    This wrapper holds a TcpMaster instance and exposes a `.connect()` method
    that simply calls `.open()` internally, so existing code that expects a
    pymodbus-shaped client (with `.connect()`) keeps working unchanged.
    """

    def __init__(self, host: str, port: int = 502):
        import modbus_tk.modbus_tcp as modbus_tcp  # lazy import for testability

        self._master = modbus_tcp.TcpMaster(host, port)

    def connect(self):
        """Open the TCP connection, mirroring pymodbus's `.connect()` semantics."""
        return self._master.open()

    @property
    def master(self):
        """Expose the underlying modbus_tk TcpMaster for use by read wrappers."""
        return self._master


class _axiom_read_result:
    """Minimal stand-in for pymodbus's response object.

    Exposes `.registers` (a list of ints) and `.isError()` (a bool), matching
    the shape that pymodbus's read_holding_registers() response provided, so
    downstream code (e.g. `decode(...)`) can remain unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


def _axiom_read_holding_registers_wrapper(client, address, count, unit):
    """Translate a pymodbus-style read_holding_registers(unit=...) call into
    modbus-tk's `master.execute(slave, cst.READ_HOLDING_REGISTERS, address, count)`.

    pymodbus's `read_holding_registers` returns a response object with
    `.registers` (list of ints) and `.isError()` (bool), raising nothing on
    failure -- errors are signalled via the `.isError()` flag.

    modbus-tk's `TcpMaster.execute(...)` instead returns a plain tuple of
    register values directly, and signals failures by *raising* an exception
    (subclasses of `modbus_tk.modbus.ModbusError` / socket errors) rather than
    setting a flag.

    This wrapper calls `execute(...)`, catches any exception, and wraps the
    result (or the failure) into an `_axiom_read_result` object exposing
    `.registers` and `.isError()`, so call sites written against the pymodbus
    shape continue to work unmodified.
    """
    import modbus_tk.defines as cst
    import modbus_tk.exceptions as modbus_tk_exceptions

    master = client.master if isinstance(client, _axiom_modbus_client_wrapper) else client
    try:
        values = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
        return _axiom_read_result(registers=list(values), error=False)
    except (modbus_tk_exceptions.ModbusError, OSError):
        return _axiom_read_result(registers=None, error=True)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_client_wrapper(host, port=port)
    client.connect()
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = _axiom_read_holding_registers_wrapper(self.client, address, count, self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
