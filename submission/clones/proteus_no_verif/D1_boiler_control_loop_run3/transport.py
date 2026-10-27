"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

class _PyModbusTCPResponseAdapter:
    """Lightweight stand-in for a pymodbus response object.

    pymodbus's read/write calls return a response object exposing
    ``.isError()`` and, for reads, ``.registers`` (a list of ints).
    pyModbusTCP's ``ModbusClient.read_holding_registers`` /
    ``write_register`` instead return the raw list of register values (for
    reads) or ``True`` (for writes) on success, and ``None``/``False`` on
    failure -- there is no response object at all.

    This adapter wraps whatever pyModbusTCP handed back so call sites that
    were written against the pymodbus API (checking ``.isError()`` and
    indexing into ``.registers``) keep working unmodified.
    """

    def __init__(self, result):
        self._result = result

    def isError(self):
        return self._result is None or self._result is False

    @property
    def registers(self):
        # pyModbusTCP returns the register list directly; pymodbus nests it
        # under `.registers`. Normalize so `.registers[0]` still works.
        if isinstance(self._result, (list, tuple)):
            return self._result
        return [self._result]


class _PymodbusCompatibleClientAdapter:
    """Adapts a pyModbusTCP ``ModbusClient`` to the pymodbus client API.

    pymodbus's ``ModbusTcpClient`` exposes ``read_holding_registers(address,
    count, unit=...)`` / ``write_register(address, value, unit=...)`` that
    return response objects with ``.isError()`` / ``.registers``, and the
    unit/slave id is passed per-call as a keyword argument.

    pyModbusTCP's ``ModbusClient`` instead takes the unit id at
    construction time (``unit_id=``), exposes the same method names but
    returns a plain list (or ``None`` on failure) for reads and a bool for
    writes, and has no per-call ``unit`` keyword.

    This wrapper accepts (and ignores, beyond a consistency check) the
    ``unit`` keyword on calls -- since pyModbusTCP already pinned the unit
    id at construction -- and packages the raw list/bool result into a
    ``_PyModbusTCPResponseAdapter`` so downstream code that expects
    pymodbus-style ``.isError()`` / ``.registers[0]`` keeps working
    unmodified.
    """

    def __init__(self, modbus_client, unit: int = 1):
        self._client = modbus_client
        self._unit = unit

    def connect(self):
        return self._client.open()

    def close(self):
        return self._client.close()

    def read_holding_registers(self, address, count=1, unit=None):
        result = self._client.read_holding_registers(address, count)
        return _PyModbusTCPResponseAdapter(result)

    def write_register(self, address, value, unit=None):
        result = self._client.write_single_register(address, value)
        return _PyModbusTCPResponseAdapter(result)


def _pymodbus_compatible_client_adapter(host: str, port: int = 502, unit: int = 1):
    """Build a pyModbusTCP client and wrap it to look like a pymodbus client.

    See ``_PymodbusCompatibleClientAdapter`` for the details of the API
    mismatch being bridged (per-call ``unit`` kwarg vs. construction-time
    ``unit_id``, and response objects vs. raw list/bool returns).
    """
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    raw_client = ModbusClient(host=host, port=port, unit_id=unit, auto_open=True)
    return _PymodbusCompatibleClientAdapter(raw_client, unit=unit)


def _default_client(host: str, port: int = 502):

    client = _pymodbus_compatible_client_adapter(host, port=port)
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
