"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _AxiomModbusResult:
    """Adapter exposing pymodbus-style `.registers` / `.isError()` from minimalmodbus's
    plain return values.

    pymodbus's `read_holding_registers()` returns a response object with a
    `.registers` list attribute and an `.isError()` method. minimalmodbus's
    `read_registers()` instead just returns a plain list of ints on success, or
    raises an exception (e.g. minimalmodbus.NoResponseError /
    minimalmodbus.InvalidResponseError) on failure -- it has no response object
    at all. This wrapper stores whichever of those two outcomes occurred and
    re-exposes it through the pymodbus-shaped `.registers` / `.isError()`
    interface, so call sites written against pymodbus don't need to change
    their error-handling logic.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _AxiomModbusClientWrapper:
    """Bridges a minimalmodbus.Instrument (one serial slave) behind the
    pymodbus ModbusTcpClient call shape (`connect()` / `read_holding_registers(
    address, count, unit=...)`) used by this module.

    pymodbus's ModbusTcpClient talks Modbus TCP to a host:port and its
    `read_holding_registers(address, count, unit=...)` can address any unit id
    over that one connection. minimalmodbus instead talks Modbus RTU/ASCII over
    a local serial line to exactly one physical instrument, identified by a
    fixed slave address chosen when the Instrument is constructed; its
    `read_registers(address, count)` call takes no `unit` argument at all, since
    switching units per call isn't meaningful for a single serial line. This
    wrapper stores a fixed slave address at construction time (defaulting to
    1, matching this module's default `unit`), ignores/accepts the `unit`
    keyword purely for call-shape compatibility, and translates each
    `read_holding_registers()` call into minimalmodbus's `read_registers()`,
    catching any raised exception and turning both outcomes into an
    `_AxiomModbusResult` so the rest of this module keeps its pymodbus-shaped
    `.registers` / `.isError()` usage unchanged.
    """

    def __init__(self, port_name, slave_address=1):
        import minimalmodbus  # local import so this stays test/import-friendly

        self._instrument = minimalmodbus.Instrument(port_name, slave_address)

    def connect(self):
        # minimalmodbus opens/closes the serial port per transaction; there is
        # no separate connect step, so this is a no-op kept for call-shape
        # compatibility with pymodbus's client.
        return True

    def read_holding_registers(self, address, count, unit=1):
        try:
            registers = self._instrument.read_registers(address, count)
            return _AxiomModbusResult(registers=registers)
        except Exception:
            return _AxiomModbusResult(error=True)


def _axiom_modbus_client_wrapper(host, port=502):
    """Wrapper bridging pymodbus's `ModbusTcpClient(host, port=...)` construction
    onto minimalmodbus.

    minimalmodbus has no TCP client: it only speaks Modbus RTU/ASCII over a
    local serial line, so there is no faithful translation of a network
    host/port pair into that transport. This wrapper treats `host` as the
    serial device path (e.g. "/dev/ttyUSB0") expected by
    minimalmodbus.Instrument, drops the TCP `port` value entirely (it has no
    meaning for a serial connection), and returns an
    `_AxiomModbusClientWrapper` exposing the same `connect()` /
    `read_holding_registers()` call shape the rest of this module expects from
    a pymodbus client.
    """
    return _AxiomModbusClientWrapper(host)


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = _axiom_modbus_client_wrapper(host, port)
    client.connect()
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self.client.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
