"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):

    return _axiom_tcp_client_factory(host, port=port)


def _axiom_tcp_client_factory(host: str, port: int = 502, timeout: float = 5.0):
    """Build the client used by ModbusIO for TCP communication.

    pymodbus's ModbusTcpClient() constructor produces an object with named
    methods such as read_holding_registers()/write_register()/connect()/close()
    that return rich response objects exposing .isError()/.registers.
    modbus-tk instead builds a modbus_tk.modbus_tcp.TcpMaster(host, port) whose
    connection is opened/closed via open()/close() and whose reads/writes all
    go through a single execute(slave, function_code, ...) call that returns
    plain tuples (or raises a modbus_tk exception on failure). This factory
    creates a TcpMaster, configures its timeout, opens the connection via
    _axiom_client_connect_wrapper (so downstream code can still "connect" the
    way it expects), and returns the master itself: the master already
    exposes execute()/open()/close(), which is exactly what the
    _axiom_read_holding_registers_wrapper/_axiom_write_register_wrapper
    helpers below expect to receive.
    """
    import modbus_tk.modbus_tcp as modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.set_timeout(timeout)
    _axiom_client_connect_wrapper(master)
    return master


def _axiom_client_connect_wrapper(master):
    """Bridge pymodbus-style client.connect() calls onto modbus-tk.

    pymodbus clients expose a .connect() method that opens the underlying
    socket and returns True/False to indicate success. modbus-tk's TcpMaster
    exposes open() instead (idempotent open of the socket, no meaningful
    return value, and it may raise if invoked while a connection attempt is
    already in a bad state). This wrapper calls open() on the master and
    treats failures as a lazy-connect no-op -- a genuine connectivity problem
    will still surface the next time execute() is called -- so call sites
    that previously did `client.connect()` keep working unchanged.
    """
    try:
        master.open()
    except Exception:
        pass
    return True


def _axiom_read_holding_registers_wrapper(master, unit, address, count=1):
    """Bridge pymodbus-style read_holding_registers() calls onto modbus-tk.

    pymodbus's client.read_holding_registers(address, count, unit=unit)
    returns a response object exposing .isError() and .registers (a list of
    the values read). modbus-tk's master.execute() instead takes the slave id
    and function code as positional arguments (execute(unit,
    cst.READ_HOLDING_REGISTERS, address, count)) and returns a plain tuple of
    register values on success, or raises a modbus_tk exception on failure.
    This wrapper performs that execute() call, catches any exception to
    synthesize an isError()-style failure, and wraps the resulting tuple in a
    small adapter exposing .isError() and .registers so existing call sites
    (rr.isError(), rr.registers[0]) continue to work without modification.
    """
    import modbus_tk.defines as cst

    class _ReadResult:
        def __init__(self, registers=None, error=False):
            self.registers = list(registers) if registers is not None else []
            self._error = error

        def isError(self):
            return self._error

    try:
        result = master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
    except Exception:
        return _ReadResult(error=True)

    return _ReadResult(registers=result, error=False)


def _axiom_write_register_wrapper(master, unit, address, value):
    """Bridge pymodbus-style write_register() calls onto modbus-tk.

    pymodbus's client.write_register(address, value, unit=unit) returns a
    response object exposing .isError(). modbus-tk's master.execute() instead
    takes the slave id and function code as positional arguments
    (execute(unit, cst.WRITE_SINGLE_REGISTER, address,
    output_value=value)) and returns the written value on success, or raises
    a modbus_tk exception on failure. This wrapper performs that execute()
    call, catches any exception to synthesize an isError()-style failure, and
    returns a small adapter exposing .isError() so existing call sites
    (wr.isError()) continue to work without modification.
    """

    class _WriteResult:
        def __init__(self, error=False):
            self._error = error

        def isError(self):
            return self._error

    try:
        master.execute(unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value)
    except Exception:
        return _WriteResult(error=True)

    return _WriteResult(error=False)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = _axiom_read_holding_registers_wrapper(self.client, self.unit, address, 1)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = _axiom_write_register_wrapper(self.client, self.unit, address, counts)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
