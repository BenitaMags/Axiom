"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

def _axiom_modbus_tcp_wrapper(host: str, port: int = 502):
    """Bridge for pymodbus's ModbusTcpClient(host, port=port) constructor.

    pymodbus returns a ModbusTcpClient object that communicates over a TCP
    socket to a Modbus TCP server at (host, port). minimalmodbus has no
    concept of a TCP client: minimalmodbus.Instrument is built around a
    local serial port device path (e.g. "/dev/ttyUSB0") and a slave address,
    and talks Modbus RTU/ASCII over that serial port. There is no faithful
    way to turn a (host, port) pair into a minimalmodbus.Instrument without
    an external serial-to-Modbus-TCP gateway, which this environment cannot
    assume exists. Rather than silently returning something that looks
    like a client but cannot actually talk to (host, port), this wrapper
    raises a clear NotImplementedError describing the incompatibility.
    """
    raise NotImplementedError(
        "minimalmodbus cannot construct a TCP client for "
        f"{host!r}:{port!r}; minimalmodbus.Instrument requires a local "
        "serial port device path. A serial-to-Modbus gateway would be "
        "needed to bridge this call, and none is configured."
    )


def _axiom_modbus_connect_wrapper(client):
    """Bridge for pymodbus's client.connect() call.

    pymodbus requires an explicit connect() call to open the TCP socket
    before any read/write can happen, and returns a bool indicating
    success. minimalmodbus.Instrument opens its serial port immediately
    upon construction (via the constructor's `port` argument) and has no
    separate connect() method -- the port is either already open or gets
    reopened automatically around each transaction. This wrapper is
    therefore a no-op unless the client exposes its own serial handle,
    in which case it makes a best effort to (re)open it, mirroring the
    "ensure connected" intent of the original pymodbus call.
    """
    serial_port = getattr(client, "serial", None)
    if serial_port is not None and hasattr(serial_port, "is_open"):
        if not serial_port.is_open:
            serial_port.open()
    return True


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = _axiom_modbus_tcp_wrapper(host, port=port)
    _axiom_modbus_connect_wrapper(client)
    return client


class _AxiomModbusResponse:
    """Minimal shim mimicking a pymodbus response object.

    Exposes .isError() and .registers so existing call sites written
    against pymodbus's response objects keep working unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


def _axiom_modbus_read_holding_registers_wrapper(client, address, unit=1):
    """Bridge for pymodbus's client.read_holding_registers(address, 1, unit=unit).

    pymodbus returns a response object with .isError() and a .registers
    list containing the requested register values. minimalmodbus's
    equivalent, Instrument.read_register(address, functioncode=3), returns
    a plain int (the register value) directly, and signals failures by
    raising exceptions (e.g. IOError, ValueError) rather than returning an
    error flag. This wrapper calls read_register, catches any exception to
    translate it into a boolean isError() result, and packages the scalar
    value into a .registers list, so callers can keep using
    rr.isError() / rr.registers[0] unchanged.
    """
    try:
        value = client.read_register(address, functioncode=3)
        return _AxiomModbusResponse(registers=[value], error=False)
    except Exception:
        return _AxiomModbusResponse(registers=[], error=True)


def _axiom_modbus_write_register_wrapper(client, address, counts, unit=1):
    """Bridge for pymodbus's client.write_register(address, counts, unit=unit).

    pymodbus returns a response object with .isError() indicating whether
    the write succeeded. minimalmodbus's Instrument.write_register(address,
    counts, functioncode=16) instead returns None on success and raises an
    exception on failure. This wrapper calls write_register, catches any
    exception to translate it into a boolean isError() flag on a shim
    response object, so callers can keep using wr.isError() unchanged.
    """
    try:
        client.write_register(address, counts, functioncode=16)
        return _AxiomModbusResponse(error=False)
    except Exception:
        return _AxiomModbusResponse(error=True)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = _axiom_modbus_read_holding_registers_wrapper(self.client, address, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = _axiom_modbus_write_register_wrapper(self.client, address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
