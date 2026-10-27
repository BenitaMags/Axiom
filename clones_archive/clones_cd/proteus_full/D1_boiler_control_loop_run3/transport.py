"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge wrapper for constructing a Modbus client from a TCP host/port pair.

    pymodbus's `ModbusTcpClient(host, port=port)` returns a connected TCP client
    object whose `read_holding_registers()` / `write_register()` methods accept
    an explicit `unit` id and return response objects exposing `.isError()` and
    `.registers`.

    minimalmodbus has no TCP transport: its `minimalmodbus.Instrument` is built
    around a *serial* port device path (e.g. "/dev/ttyUSB0") plus a slave
    address, not a network host/port pair. There is therefore no faithful,
    like-for-like way to turn `(host, port)` into a minimalmodbus Instrument --
    doing so would require an actual serial device (or a serial-over-TCP proxy)
    which this call site does not have.

    Rather than silently fabricating a bogus serial device string or returning
    an object that would fail unpredictably on first use, this wrapper raises a
    clear, explicit error describing the incompatibility so callers can supply
    a real serial device path instead.
    """
    raise NotImplementedError(
        "Cannot bridge TCP host/port "
        f"({host!r}, {port!r}) to minimalmodbus: minimalmodbus.Instrument "
        "requires a serial device path (e.g. '/dev/ttyUSB0') and slave "
        "address, not a network host/port pair. Supply a serial device "
        "instead of a TCP host/port."
    )


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
