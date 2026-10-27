"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    # NOTE: minimalmodbus only ever talks to an instrument over a local serial
    # port (e.g. "/dev/ttyUSB0" or "COM3"); it has no notion of a TCP host:port
    # endpoint at all. There is therefore no glue code that can turn a
    # (host, port) pair into a working minimalmodbus connection -- the
    # underlying transport itself is incompatible, not just the call shape.
    # This constructor is intentionally left calling the original client;
    # only the read/write call sites below (which *can* be bridged) are
    # routed through `_axiom_modbus_tcp_to_serial_wrapper`.
    client = ModbusTcpClient(host, port=port)
    client.connect()
    return client


def _axiom_modbus_response(value=None, error=None):
    """Small stand-in for a pymodbus response object.

    pymodbus read/write calls return a response object exposing
    `.isError()` and (for reads) `.registers`. minimalmodbus's
    `read_register`/`write_register` calls instead return a plain int (or
    None) on success and raise an exception on failure. This helper wraps
    either a successful value or a caught exception into an object with the
    same `.isError()`/`.registers` shape the rest of this module expects, so
    call sites written against the pymodbus response API don't need to
    change their error-handling logic.
    """

    class _Resp:
        def __init__(self, value, error):
            self._error = error
            self.registers = [value] if value is not None else []

        def isError(self):
            return self._error is not None

    return _Resp(value, error)


def _axiom_modbus_tcp_to_serial_wrapper(client, mode, address, value=None, unit=1):
    """Bridge pymodbus-style read/write calls onto minimalmodbus's API.

    pymodbus: `client.read_holding_registers(address, count, unit=...)` and
    `client.write_register(address, value, unit=...)` both return a response
    object exposing `.isError()` (and `.registers` for reads).

    minimalmodbus: `instrument.read_register(address, ...)` returns the
    register value directly (an int) and `instrument.write_register(address,
    value, ...)` returns None; both raise an exception (e.g. IOError,
    ValueError, or a minimalmodbus-specific error) instead of returning an
    error flag.

    This wrapper calls the minimalmodbus-style methods and translates a
    successful return value, or a raised exception, into the
    `.isError()`/`.registers` shape the call sites below already expect, so
    those call sites do not need to be rewritten to a different
    error-handling style.

    Note: this only bridges the read/write call *shape*. It cannot fabricate
    minimalmodbus support for TCP host:port connectivity -- see
    `_default_client` above, which minimalmodbus fundamentally cannot
    replace because it is a serial-only library.
    """
    try:
        if mode == "read":
            result = client.read_register(address)
            return _axiom_modbus_response(value=result)
        elif mode == "write":
            client.write_register(address, value)
            return _axiom_modbus_response(value=value)
        else:
            raise ValueError(f"unknown mode {mode!r}")
    except Exception as exc:  # translate minimalmodbus exceptions to isError()
        return _axiom_modbus_response(error=exc)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = _axiom_modbus_tcp_to_serial_wrapper(
            self.client, "read", address, unit=self.unit
        )
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = _axiom_modbus_tcp_to_serial_wrapper(
            self.client, "write", address, value=counts, unit=self.unit
        )
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
