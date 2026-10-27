"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_read_holding_registers_wrapper(master, unit, address, count):
    """
    pymodbus's client.read_holding_registers(address, count, unit=unit) returns
    a response object exposing .isError() (True when the slave replied with a
    Modbus protocol exception) and .registers (the list of values read).

    modbus-tk's master.execute(unit, cst.READ_HOLDING_REGISTERS, address,
    count) instead returns a plain tuple of register values on success, and
    raises a modbus_tk.exceptions.ModbusError subclass (protocol-level
    failures such as an illegal address/value reply, or a malformed/
    unparsable response) when something goes wrong -- there is no status
    object to inspect. This wrapper calls execute() and, on a ModbusError,
    builds a fake response whose isError() is True and registers is empty,
    matching what the call sites already check. Genuine transport failures
    (socket timeouts/connection errors) are NOT caught here and propagate
    to the caller, the same way pymodbus itself raises a ConnectionException
    for those instead of returning an isError() response.
    """
    import modbus_tk.defines as cst
    import modbus_tk.exceptions as mt_exceptions

    class _FakeReadResponse:
        def __init__(self, registers=None, errored=False):
            self.registers = list(registers) if registers is not None else []
            self._errored = errored

        def isError(self):
            return self._errored

    try:
        result = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
    except mt_exceptions.ModbusError:
        return _FakeReadResponse(errored=True)
    return _FakeReadResponse(registers=result)


def _axiom_write_register_wrapper(master, unit, address, value):
    """
    pymodbus's client.write_register(address, value, unit=unit) returns a
    response object exposing .isError() (True when the slave replied with a
    Modbus protocol exception).

    modbus-tk's master.execute(unit, cst.WRITE_SINGLE_REGISTER, address,
    output_value=value) instead returns the (address, value) tuple echoed by
    the slave on success, and raises a modbus_tk.exceptions.ModbusError
    subclass on a protocol-level failure -- again, no status object. This
    wrapper performs the write and, on a ModbusError, returns a fake response
    whose isError() is True, matching what the call site checks. Transport
    failures (timeouts, socket errors) are left uncaught and propagate,
    mirroring pymodbus raising a ConnectionException for those instead of
    returning isError().
    """

    import modbus_tk.defines as cst  # connector: bound to this scope
    import modbus_tk.exceptions as mt_exceptions  # connector: bound to this scope
    class _FakeWriteResponse:
        def __init__(self, errored=False):
            self._errored = errored

        def isError(self):
            return self._errored

    try:
        master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value)
    except mt_exceptions.ModbusError:
        return _FakeWriteResponse(errored=True)
    return _FakeWriteResponse(errored=False)


def _axiom_modbus_client_factory(host: str, port: int = 502):
    """
    pymodbus's ModbusTcpClient(host, port=port) returns a client object with
    .connect() to open the TCP transport, and .read_holding_registers()/
    .write_register() methods (used elsewhere in this module) that return
    response objects exposing .isError()/.registers.

    modbus-tk has no equivalent single client class: modbus_tk.modbus_tcp.
    TcpMaster(host, port) is opened via .open() rather than .connect(), and
    reads/writes both go through .execute(unit, function_code, address, ...)
    instead of named methods. This factory wraps a TcpMaster in a small
    adapter that re-exposes connect()/read_holding_registers()/
    write_register() with pymodbus's call signatures, delegating the actual
    translation (and exception-to-isError() conversion) to
    _axiom_read_holding_registers_wrapper and _axiom_write_register_wrapper,
    so the rest of this module (ModbusIO) does not need to change its logic.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    class _AxiomModbusTcpAdapter:
        def __init__(self, host, port):
            import modbus_tk.modbus_tcp as modbus_tcp  # connector: bound to this scope
            self._master = modbus_tcp.TcpMaster(host, port)

        def connect(self):
            self._master.open()

        def read_holding_registers(self, address, count, unit=1):
            return _axiom_read_holding_registers_wrapper(self._master, unit, address, count)

        def write_register(self, address, value, unit=1):
            return _axiom_write_register_wrapper(self._master, unit, address, value)

    return _AxiomModbusTcpAdapter(host, port)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_factory(host, port=port)
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
