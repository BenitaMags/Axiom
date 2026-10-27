"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


class _AxiomModbusResult:
    """Minimal stand-in for a pymodbus response object.

    pymodbus read/write calls return a response object exposing `.isError()`
    (bool) and, for reads, a `.registers` list of ints. minimalmodbus instead
    returns the register value(s) directly on success and raises an exception
    (e.g. IOError/ValueError) on failure. This tiny shim re-wraps a
    minimalmodbus result (or a caught exception) so downstream code that
    still expects the pymodbus-shaped `.isError()`/`.registers` interface
    keeps working unchanged.
    """

    def __init__(self, registers=None, error=None):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


class _AxiomModbusTcpAdapter:
    """Fakes a pymodbus `ModbusTcpClient`-shaped interface on top of a
    minimalmodbus `Instrument`.

    pymodbus's `ModbusTcpClient` exposes `connect()`, and
    `read_holding_registers(address, count, unit=...)` /
    `write_register(address, value, unit=...)` calls that return response
    objects with `.isError()`/`.registers`. minimalmodbus's `Instrument`
    instead exposes `read_registers(registeraddress, numberOfRegisters,
    functioncode)` / `write_register(registeraddress, value, functioncode)`
    that return plain values and raise exceptions on failure, and it only
    talks over a local serial port (it has no TCP/socket transport at all).
    This adapter translates the call shapes so existing call sites do not
    need to change their error-handling style, catching exceptions from
    minimalmodbus and turning them into `_AxiomModbusResult.isError() ==
    True` instead.
    """

    def __init__(self, instrument, unit: int):
        self._instrument = instrument
        self.unit = unit

    def connect(self):
        # minimalmodbus opens/closes the serial port per transaction (or
        # keeps it open, depending on configuration); there is no separate
        # explicit "connect" step, so this is a no-op kept for interface
        # compatibility with pymodbus's ModbusTcpClient.connect().
        return True

    def read_holding_registers(self, address, count, unit=None):
        try:
            if count == 1:
                value = self._instrument.read_register(address, functioncode=3)
                registers = [value]
            else:
                registers = self._instrument.read_registers(
                    address, count, functioncode=3
                )
            return _AxiomModbusResult(registers=registers)
        except Exception as exc:  # pragma: no cover - defensive glue
            return _AxiomModbusResult(error=exc)

    def write_register(self, address, value, unit=None):
        try:
            self._instrument.write_register(address, value, functioncode=6)
            return _AxiomModbusResult()
        except Exception as exc:  # pragma: no cover - defensive glue
            return _AxiomModbusResult(error=exc)


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502, unit: int = 1):
    """Wrapper bridging the pymodbus `ModbusTcpClient(host, port=port)` call
    to minimalmodbus.

    pymodbus's `ModbusTcpClient` connects to a remote Modbus TCP server at
    `host:port`. minimalmodbus has no network transport whatsoever -- its
    `Instrument` class only ever talks to a local serial device identified
    by a port name (e.g. "/dev/ttyUSB0" or "COM3"). There is therefore no
    way to genuinely preserve host/port TCP semantics here; `host` is passed
    through as the local serial port identifier expected by
    `minimalmodbus.Instrument`, `port` is accepted only to keep this
    function's call signature compatible with the old TCP client
    constructor, and the resulting `Instrument` is wrapped in
    `_AxiomModbusTcpAdapter` so callers can keep using the
    `connect()`/`read_holding_registers()`/`write_register()` shapes they
    already use.
    """
    import minimalmodbus

    instrument = minimalmodbus.Instrument(host, unit)
    return _AxiomModbusTcpAdapter(instrument, unit)


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = _axiom_modbus_tcp_client_wrapper(host, port=port)
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
