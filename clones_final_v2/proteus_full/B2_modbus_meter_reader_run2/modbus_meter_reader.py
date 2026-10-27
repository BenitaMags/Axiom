"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomModbusTkClientAdapter:
    """Thin adapter around a modbus_tk TcpMaster.

    pymodbus's ModbusTcpClient exposes a `.connect()` method that opens the
    underlying socket. modbus_tk's TcpMaster instead exposes `.open()` for
    the same purpose. This adapter wraps a TcpMaster instance and translates
    `.connect()` calls into `.open()` calls so downstream code written
    against the pymodbus client API needs no further changes. The wrapped
    master is reachable via the `.master` attribute for other wrappers that
    need to issue requests through it.
    """

    def __init__(self, master):
        self.master = master

    def connect(self):
        self.master.open()


def _axiom_modbustk_client_factory(host: str, port: int = 502):
    """Create a modbus_tk TcpMaster for `host`/`port` and adapt it.

    modbus-tk's client object (TcpMaster) is constructed and used quite
    differently from pymodbus's ModbusTcpClient: it must be created via
    `modbus_tcp.TcpMaster(host=..., port=...)` and opened via `.open()`
    rather than `.connect()`. This factory builds the TcpMaster and wraps
    it in `_AxiomModbusTkClientAdapter` so callers can keep using the
    pymodbus-style `.connect()` call.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)
    return _AxiomModbusTkClientAdapter(master)


class _AxiomModbusResult:
    """A pymodbus-response-like object synthesized from modbus_tk results.

    pymodbus's read_holding_registers returns a response object exposing
    `.registers` (a list/tuple of ints) and `.isError()` (a bool). This
    minimal stand-in provides the same two members so existing call sites
    written against the pymodbus API keep working unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


def _axiom_read_holding_registers_wrapper(client, address, count, unit=1):
    """Translate a pymodbus-style read_holding_registers() call to modbus_tk.

    pymodbus: `client.read_holding_registers(address, count, unit=unit)`
    returns a response object with `.registers` and `.isError()`.

    modbus-tk: reads are performed via
    `master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)`,
    which returns a plain tuple of register values on success and raises a
    modbus_tk exception (e.g. `modbus_tk.exceptions.ModbusError`) on
    failure, instead of returning an error flag.

    This wrapper calls execute() on the modbus_tk master wrapped by
    `client` (a `_AxiomModbusTkClientAdapter`, or a bare master object),
    catches any exception raised during the request, and returns an
    `_AxiomModbusResult` exposing `.registers` and `.isError()` so the
    existing control flow (`if result.isError(): ...` /
    `decode(encoding, result.registers, scale)`) continues to work without
    modification.
    """
    import modbus_tk.defines as cst

    master = getattr(client, "master", client)

    try:
        registers = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
        return _AxiomModbusResult(registers=registers, error=False)
    except Exception:
        return _AxiomModbusResult(registers=None, error=True)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbustk_client_factory(host, port=port)
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
            result = _axiom_read_holding_registers_wrapper(self.client, address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
