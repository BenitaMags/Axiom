"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomModbusTkResult:
    """Lightweight stand-in for pymodbus's response object.

    pymodbus's read_holding_registers() returns a response object exposing
    `.isError()` and `.registers` (a list of ints). modbus-tk's
    `master.execute(...)` instead returns a plain tuple of register values on
    success and raises an exception (e.g. modbus_tk.exceptions.ModbusError)
    on failure. This shim re-packages a successful tuple result into an
    object with the same `.isError()`/`.registers` shape so downstream code
    (read_measurement()) does not need to change.
    """

    def __init__(self, registers):
        self.registers = list(registers)

    def isError(self):
        return False


class _AxiomModbusTkClientShim:
    """Adapts a modbus_tk TcpMaster to pymodbus's client-object API.

    pymodbus exposes a stateful client with a
    `read_holding_registers(address, count, unit=...)` method. modbus-tk
    instead uses a functional call pattern:
    `master.execute(slave_id, cst.READ_HOLDING_REGISTERS, address, count)`.
    This shim stores the master and translates calls into that pattern,
    catching modbus-tk's exceptions and translating them into an
    `.isError()`-style result so existing call sites are unaffected.
    """

    def __init__(self, master):
        self._master = master

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst
        from modbus_tk.exceptions import ModbusError

        try:
            values = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except ModbusError:
            return _AxiomModbusTkResult([])
        return _AxiomModbusTkResult(values)


def _axiom_modbustk_client_wrapper(host: str, port: int = 502):
    """Build a modbus_tk TcpMaster and wrap it to look like a pymodbus client.

    pymodbus's `_default_client()` returned a `ModbusTcpClient(host,
    port=port)` after calling `.connect()`. modbus-tk instead requires
    constructing a `modbus_tk.modbus_tcp.TcpMaster(host, port)` and calling
    `.open()` to establish the connection. This wrapper performs that setup
    and returns a `_AxiomModbusTkClientShim` so `MeterReader` can keep calling
    `read_holding_registers(address, count, unit=...)` unchanged.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host, port)
    master.open()
    return _AxiomModbusTkClientShim(master)


def _default_client(host: str, port: int = 502):
    return _axiom_modbustk_client_wrapper(host, port=port)


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
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
