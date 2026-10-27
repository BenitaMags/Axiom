"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _axiom_modbus_client_wrapper(host: str, port: int = 502, unit: int = 1):
    """Bridge minimalmodbus's API to the shape this module expects from pymodbus.

    pymodbus's ``ModbusTcpClient`` exposes ``.connect()`` and
    ``.read_holding_registers(address, count, unit=...)``, where the latter
    returns a response object with ``.isError()`` and ``.registers``.

    minimalmodbus instead exposes an ``Instrument`` object whose
    ``.read_registers(address, count, functioncode=3)`` method returns a
    plain list of register values directly, and raises exceptions (e.g.
    ``minimalmodbus.NoResponseError``, ``IOError``, ``ValueError``) on
    failure instead of returning an error flag.

    This wrapper constructs a ``minimalmodbus.Instrument`` in TCP mode using
    ``(host, port)`` as the port argument and ``unit`` as the slave address,
    then returns a small shim object that re-exposes the pymodbus-shaped
    interface: a no-op ``.connect()`` (minimalmodbus opens/closes the
    connection per call, so there is nothing to do up front) and a
    ``.read_holding_registers(address, count, unit=...)`` method that calls
    ``instrument.read_registers(address, count, functioncode=3)`` and
    translates the result (or any raised exception) into an object with
    ``.isError()`` and ``.registers`` attributes, so callers written against
    the pymodbus response shape keep working unchanged.
    """
    import minimalmodbus

    instrument = minimalmodbus.Instrument(
        (host, port), unit, mode=minimalmodbus.MODE_TCP
    )

    class _Result:
        def __init__(self, registers=None, error=False):
            self.registers = registers if registers is not None else []
            self._error = error

        def isError(self):
            return self._error

    class _ClientShim:
        def __init__(self, instrument):
            self._instrument = instrument

        def connect(self):
            # minimalmodbus manages the connection per-call; nothing to do.
            return None

        def read_holding_registers(self, address, count, unit=1):
            try:
                registers = self._instrument.read_registers(
                    address, count, functioncode=3
                )
                return _Result(registers=registers, error=False)
            except (minimalmodbus.MasterReportedException,
                    minimalmodbus.SlaveReportedException,
                    IOError, ValueError):
                return _Result(registers=[], error=True)

    return _ClientShim(instrument)


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = _axiom_modbus_client_wrapper(host, port=port)
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
