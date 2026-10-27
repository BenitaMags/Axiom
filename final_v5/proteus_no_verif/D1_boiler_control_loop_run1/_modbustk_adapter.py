"""_modbustk_adapter -- pymodbus-shaped glue over modbus_tk.

Extracted from transport.py: this module owns the translation layer that
lets code written against pymodbus's `ModbusTcpClient` API run unchanged
against a modbus_tk `TcpMaster` instead. All modbus_tk imports remain
deferred inside the functions/methods that need them, so this module (and
transport.py, which imports it) can still be imported even when modbus_tk
is not installed.
"""


class _ModbusTkResponse:
    """Pymodbus-shaped response object.

    pymodbus's read/write calls return an object exposing `.isError()` and,
    for reads, `.registers` (a list of ints). modbus_tk's `Master.execute()`
    instead either returns a plain tuple of register values on success or
    raises a `modbus_tk.modbus.ModbusError` (or a transport-level
    `socket.error`/`socket.timeout`) on failure. This class re-packages
    either outcome into the pymodbus-shaped object so call sites written
    against pymodbus's response API keep working unchanged.
    """

    def __init__(self, registers=None, error=None):
        self.registers = registers
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


class _ModbusTkClientAdapter:
    """Adapter exposing a pymodbus-like `ModbusTcpClient` surface backed by modbus_tk.

    pymodbus's `ModbusTcpClient` exposes `.connect()`,
    `.read_holding_registers(address, count, unit=...)` and
    `.write_register(address, value, unit=...)`, all returning a response
    object with `.isError()`/`.registers`. modbus_tk's `TcpMaster` instead
    exposes `.open()`/`.close()` and a single `.execute(slave_id,
    function_code, starting_address, quantity_of_x=..., output_value=...)`
    method that returns a raw tuple on success and raises on failure. This
    adapter translates the pymodbus-shaped calls into the equivalent
    `execute()` calls with the right modbus_tk function codes, and wraps the
    tuple result (or a caught error) into `_ModbusTkResponse` so the rest of
    the file's call chain (`rr.isError()` / `rr.registers[0]`) is unaffected
    by the underlying library swap.
    """

    def __init__(self, master):
        self._master = master

    def connect(self) -> bool:
        self._master.open()
        return True

    def close(self):
        self._master.close()

    def read_holding_registers(self, address, count, unit: int = 1) -> "_ModbusTkResponse":
        import socket

        import modbus_tk.defines as cst
        import modbus_tk.modbus as modbus_tk_modbus

        try:
            result = self._master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
            return _ModbusTkResponse(registers=list(result))
        except (modbus_tk_modbus.ModbusError, socket.error) as exc:
            return _ModbusTkResponse(error=exc)

    def write_register(self, address, value, unit: int = 1) -> "_ModbusTkResponse":

        try:
            self._master.execute(
                unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
            return _ModbusTkResponse(registers=[value])
        except (modbus_tk_modbus.ModbusError, socket.error) as exc:
            return _ModbusTkResponse(error=exc)


def _modbustk_client_adapter(host: str, port: int = 502) -> "_ModbusTkClientAdapter":
    """Build a modbus_tk `TcpMaster` and wrap it as a pymodbus-shaped client.

    pymodbus's `ModbusTcpClient(host, port=port)` returns an object that is
    later driven through `.connect()`, `.read_holding_registers(...)` and
    `.write_register(...)`, each returning a `.isError()`/`.registers`
    response. modbus_tk has no equivalent client: `TcpMaster(host, port)`
    must be opened with `.open()` and driven through `.execute()` with
    explicit function codes. This helper constructs the `TcpMaster` and
    returns a `_ModbusTkClientAdapter` that re-exposes the pymodbus-shaped
    methods the rest of the module already calls, so `ModbusIO` does not
    need to know which library is underneath.
    """
    import modbus_tk.modbus_tcp as modbus_tk_tcp  # lazy import for testability

    master = modbus_tk_tcp.TcpMaster(host, port)
    return _ModbusTkClientAdapter(master)
