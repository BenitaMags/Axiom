"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _modbustk_response(registers=None, is_error=False):
    """Small pymodbus-style response holder.

    pymodbus's read/write calls return an object exposing `.isError()` and,
    for reads, `.registers` (a list of ints). modbus-tk's `Master.execute()`
    instead returns a plain tuple of register values on success and *raises*
    an exception on failure (no error object at all). This helper packages
    either a successful result or a translated failure into the same
    `.isError()` / `.registers` shape pymodbus produced, so call sites written
    against pymodbus's return-object style keep working unchanged.
    """

    class _Response:
        def __init__(self, registers, is_error):
            self.registers = registers if registers is not None else []
            self._is_error = is_error

        def isError(self):
            return self._is_error

    return _Response(registers, is_error)


class _ModbusTkClientShim:
    """Adapts modbus_tk's TcpMaster to the pymodbus ModbusTcpClient API used here.

    pymodbus's `ModbusTcpClient` exposes `.connect()` plus
    `.read_holding_registers(address, count, unit=...)` and
    `.write_register(address, value, unit=...)`, both returning response
    objects with `.isError()` (and, for reads, `.registers`). modbus-tk's
    `TcpMaster` instead exposes `.open()` for connecting and a single
    `.execute(slave, function_code, address, quantity_of_x=..., output_value=...)`
    call that returns a tuple of register values on success and *raises* one
    of modbus-tk's exceptions on failure -- these are not all one hierarchy
    (protocol errors live under `modbus_tk.exceptions.ModbusError`, while
    transport failures surface as plain socket errors or `struct.error` from
    unpacking a short/malformed frame), so every one of those has to be
    caught to match what pymodbus reported as `isError()`. This shim
    translates the call shape (address/count/unit -> execute(slave,
    function_code, address, ...)) and converts any of those raised exceptions
    into the same isError()-style response pymodbus would have returned, so
    `ModbusIO._read1` and `write_valve` do not need to change their
    error-checking style.
    """

    def __init__(self, host, port=502):
        import struct  # lazy import for testability
        import modbus_tk.defines as cst  # lazy import for testability
        import modbus_tk.exceptions as mtk_exceptions  # lazy import for testability
        import modbus_tk.modbus_tcp as modbus_tcp  # lazy import for testability

        self._cst = cst
        self._errors = (mtk_exceptions.ModbusError, OSError, struct.error)
        self._master = modbus_tcp.TcpMaster(host, port)

    def connect(self):
        self._master.open()
        return True

    def read_holding_registers(self, address, count, unit=1):
        try:
            values = self._master.execute(
                unit, self._cst.READ_HOLDING_REGISTERS, address, count
            )
        except self._errors:
            return _modbustk_response(is_error=True)
        return _modbustk_response(registers=list(values), is_error=False)

    def write_register(self, address, value, unit=1):
        try:
            self._master.execute(
                unit,
                self._cst.WRITE_SINGLE_REGISTER,
                address,
                output_value=value,
            )
        except self._errors:
            return _modbustk_response(is_error=True)
        return _modbustk_response(is_error=False)


def _modbustk_client_shim(host: str, port: int = 502):
    """Build a pymodbus-ModbusTcpClient-shaped client backed by modbus-tk.

    Replaces `pymodbus.client.ModbusTcpClient(host, port=port)` with a
    `_ModbusTkClientShim` that exposes the same `.connect()`,
    `.read_holding_registers(...)`, and `.write_register(...)` surface, so the
    rest of this module (which was written against pymodbus's API) does not
    need to change.
    """

    client = _ModbusTkClientShim(host, port=port)
    client.connect()
    return client


def _default_client(host: str, port: int = 502):
    return _modbustk_client_shim(host, port=port)


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
