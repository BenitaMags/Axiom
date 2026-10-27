"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _pymodbus_compatible_client_wrapper(host: str, port: int = 502, unit: int = 1):
    """Build a pyModbusTCP-backed client that behaves like a pymodbus client.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()`` to open the
    connection and ``read_holding_registers(address, count, unit=...)``,
    which returns a response object with ``.isError()`` and ``.registers``.

    pyModbusTCP's ``ModbusClient`` instead exposes ``open()`` to connect (no
    ``connect()`` method), configures the unit id on the client itself
    (``unit_id``) rather than accepting a ``unit=`` keyword per call, and
    ``read_holding_registers(address, count)`` returns a plain list of
    register values, or ``None`` if the read failed -- there is no response
    object and no ``.isError()``/``.registers``.

    This wrapper constructs a ``pyModbusTCP.client.ModbusClient``, adds a
    ``connect()`` method that delegates to ``.open()`` so callers don't need
    to change their connection lifecycle code, and wraps
    ``read_holding_registers`` so it still accepts the ``unit=`` keyword
    (applied to the underlying client's ``unit_id`` before the read) and
    returns a small ``_Result`` object exposing ``.isError()`` (True when
    pyModbusTCP returned ``None``) and ``.registers`` (the returned list),
    so ``read_measurement()`` keeps working unmodified.
    """
    from pyModbusTCP.client import ModbusClient

    class _Result:
        def __init__(self, registers):
            self._registers = registers

        def isError(self):
            return self._registers is None

        @property
        def registers(self):
            return self._registers

    class _ClientAdapter:
        def __init__(self, host, port, unit):
            self._client = ModbusClient(host=host, port=port, unit_id=unit, auto_open=False)

        def connect(self):
            return self._client.open()

        def close(self):
            self._client.close()

        def read_holding_registers(self, address, count, unit=None):
            if unit is not None:
                self._client.unit_id = unit
            registers = self._client.read_holding_registers(address, count)
            return _Result(registers)

    return _ClientAdapter(host, port, unit)


def _default_client(host: str, port: int = 502):

    client = _pymodbus_compatible_client_wrapper(host, port=port)
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
