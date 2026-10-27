"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge wrapper standing in for `ModbusTcpClient(host, port=port)`.

    pymodbus's `ModbusTcpClient(host, port=port)` opens a TCP connection and
    returns a client object whose `read_holding_registers()` /
    `write_register()` methods return response objects that are checked via
    `.isError()` and (for reads) exposed through a `.registers` list.

    minimalmodbus has no TCP transport at all. Its `minimalmodbus.Instrument`
    is constructed from a local serial device path (e.g. "/dev/ttyUSB0" or
    "COM3") plus a numeric slave address, and its `read_register()` /
    `write_register()` calls raise exceptions on failure instead of returning
    an `isError()`-checkable object.

    This wrapper cannot fully close that gap in pure code -- reaching a real
    TCP Modbus device would require an external TCP-to-serial gateway, which
    is infrastructure, not glue. What it *does* do is the part that can be
    expressed in code: it treats `host` as the serial device path and `port`
    as the minimalmodbus slave address, constructs the `Instrument`
    accordingly, and returns a small adapter object that re-exposes
    `read_holding_registers()` / `write_register()` / `.connect()` with the
    same isError()/.registers shape the rest of this module expects, so call
    sites written against the pymodbus-style API keep working unchanged.
    """
    import minimalmodbus

    instrument = minimalmodbus.Instrument(host, port)

    class _CompatResponse:
        def __init__(self, registers=None, error=False):
            self.registers = registers if registers is not None else []
            self._error = error

        def isError(self):
            return self._error

    class _CompatClient:
        def __init__(self, instrument):
            self._instrument = instrument

        def connect(self):
            # minimalmodbus opens the serial port lazily on first access;
            # nothing to do eagerly, but keep the pymodbus-style call site
            # working unchanged.
            return True

        def read_holding_registers(self, address, count, unit=1):
            try:
                values = [
                    self._instrument.read_register(
                        address + i, number_of_decimals=0, functioncode=3
                    )
                    for i in range(count)
                ]
                return _CompatResponse(registers=values)
            except Exception:
                return _CompatResponse(error=True)

        def write_register(self, address, value, unit=1):
            try:
                self._instrument.write_register(
                    address, value, number_of_decimals=0, functioncode=6
                )
                return _CompatResponse()
            except Exception:
                return _CompatResponse(error=True)

    return _CompatClient(instrument)


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
