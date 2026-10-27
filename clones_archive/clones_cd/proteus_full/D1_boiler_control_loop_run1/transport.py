"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_wrapper(host, port: int = 502, unit: int = 1):
    """Bridge for constructing a client given a pymodbus-style (host, port) pair.

    pymodbus's `ModbusTcpClient(host, port=port)` builds a TCP transport object
    that is later explicitly opened via `.connect()`. minimalmodbus has no
    network transport at all: `minimalmodbus.Instrument(port, slaveaddress)`
    takes a *serial device identifier* (e.g. "/dev/ttyUSB0" or "COM3") and a
    numeric slave address, and it opens the serial port immediately at
    construction time -- there is no separate connect step.

    This wrapper treats the incoming `host` value as the serial device
    identifier minimalmodbus expects (the closest available analogue, since
    minimalmodbus cannot faithfully preserve TCP transport semantics), builds
    the Instrument with `unit` as the slave address, and attaches a no-op
    `.connect()` method so existing call sites that call `client.connect()`
    continue to work unchanged.
    """
    import minimalmodbus

    instrument = minimalmodbus.Instrument(host, unit)
    instrument.connect = lambda: None
    return instrument


def _axiom_read_holding_registers_wrapper(client, address, unit: int = 1):
    """Bridge for reading a single holding register.

    pymodbus's `read_holding_registers(address, count, unit=...)` returns a
    response object exposing `.isError()` and a `.registers` list of the
    values read. minimalmodbus's `read_register(address)` instead returns the
    integer value directly on success, or raises an exception on failure --
    it has no `.isError()`/`.registers` result object and no `unit` kwarg
    (the slave address lives on the Instrument itself).

    This wrapper calls minimalmodbus's `read_register`, catches any
    exception it might raise, and returns a small shim object that exposes
    `.isError()` and `.registers` so existing call-chain code
    (`rr.isError()`, `rr.registers[0]`) keeps working unchanged.
    """

    class _ReadResult:
        def __init__(self, value=None, error=False):
            self._value = value
            self._error = error

        def isError(self):
            return self._error

        @property
        def registers(self):
            return [self._value if self._value is not None else 0]

    try:
        value = client.read_register(address)
        return _ReadResult(value=value, error=False)
    except Exception:
        return _ReadResult(error=True)


def _axiom_write_register_wrapper(client, address, counts, unit: int = 1):
    """Bridge for writing a single holding register.

    pymodbus's `write_register(address, value, unit=...)` returns a response
    object exposing `.isError()`. minimalmodbus's `write_register(address,
    value)` instead returns `None` on success and raises an exception on
    failure, and it has no `unit` kwarg (the slave address lives on the
    Instrument itself, so it is dropped here rather than forwarded).

    This wrapper calls minimalmodbus's `write_register`, catches any
    exception it might raise, and returns a small shim object that exposes
    `.isError()` so existing call-chain code (`wr.isError()`) keeps working
    unchanged.
    """

    class _WriteResult:
        def __init__(self, error=False):
            self._error = error

        def isError(self):
            return self._error

    try:
        client.write_register(address, counts)
        return _WriteResult(error=False)
    except Exception:
        return _WriteResult(error=True)


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = _axiom_modbus_client_wrapper(host, port=port)
    client.connect()
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = _axiom_read_holding_registers_wrapper(self.client, address, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = _axiom_write_register_wrapper(self.client, address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
