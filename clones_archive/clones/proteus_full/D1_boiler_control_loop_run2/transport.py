"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


class _AxiomModbusReadResult:
    """Response-like wrapper around a pyModbusTCP holding-register read.

    pymodbus's `read_holding_registers` returns a response object exposing
    `.isError()` and a `.registers` list of the values read. pyModbusTCP's
    `read_holding_registers` instead returns a plain Python list of register
    values on success, or `None` on failure. This wrapper normalizes the
    pyModbusTCP result back into an object with `.isError()` and `.registers`
    so call sites written against the pymodbus response shape keep working.
    """

    def __init__(self, registers):
        self._registers = registers

    def isError(self) -> bool:
        return self._registers is None

    @property
    def registers(self):
        return self._registers if self._registers is not None else []


class _AxiomModbusWriteResult:
    """Response-like wrapper around a pyModbusTCP single-register write.

    pymodbus's `write_register` returns a response object exposing
    `.isError()`. pyModbusTCP's `write_register` instead returns `True` on
    success, `False` on failure, or `None` if the client isn't connected.
    This wrapper normalizes that boolean/None result back into an object
    with `.isError()` so call sites written against the pymodbus response
    shape keep working.
    """

    def __init__(self, success):
        self._success = bool(success)

    def isError(self) -> bool:
        return not self._success


class _AxiomModbusClientAdapter:
    """Adapts a pyModbusTCP ModbusClient to the pymodbus client API surface.

    pymodbus's `ModbusTcpClient` is constructed with `host`/`port` and used
    via `.connect()`, `.read_holding_registers(address, count, unit=...)`
    and `.write_register(address, value, unit=...)`, where `unit` is passed
    per-call. pyModbusTCP's `ModbusClient` instead takes `host`/`port` plus
    an `auto_open` flag, is opened via `.open()`, and keeps the unit/slave id
    as a persistent `unit_id` attribute on the client rather than a per-call
    keyword argument, with `read_holding_registers`/`write_register` never
    accepting `unit` themselves. This adapter stores the underlying
    pyModbusTCP client, applies any per-call `unit` onto `client.unit_id`
    before delegating, and wraps the raw list/bool/None results into the
    response-like objects above so existing `rr.isError()`, `rr.registers`
    and `wr.isError()` call sites keep working unchanged.
    """

    def __init__(self, client, unit: int = 1):
        self._client = client
        self._client.unit_id = unit

    def connect(self):
        return self._client.open()

    def read_holding_registers(self, address, count, unit=None):
        if unit is not None:
            self._client.unit_id = unit
        registers = self._client.read_holding_registers(address, count)
        return _AxiomModbusReadResult(registers)

    def write_register(self, address, value, unit=None):
        if unit is not None:
            self._client.unit_id = unit
        success = self._client.write_register(address, value)
        return _AxiomModbusWriteResult(success)


def _axiom_modbus_client_wrapper(client_cls, host: str, port: int = 502, unit: int = 1):
    """Build a pymodbus-compatible client wrapper around a pyModbusTCP client.

    Constructs `client_cls(host=host, port=port, auto_open=True)` -- the
    pyModbusTCP constructor shape -- opens it, and returns an
    `_AxiomModbusClientAdapter` exposing the `connect()` /
    `read_holding_registers(address, count, unit=...)` /
    `write_register(address, value, unit=...)` methods that pymodbus-style
    callers expect, bridging the constructor and per-call `unit` handling
    differences described above.
    """

    client = client_cls(host=host, port=port, auto_open=True)
    adapter = _AxiomModbusClientAdapter(client, unit=unit)
    adapter.connect()
    return adapter


def _default_client(host: str, port: int = 502, unit: int = 1):
    from pyModbusTCP import ModbusTcpClient  # lazy import for testability

    return _axiom_modbus_client_wrapper(ModbusTcpClient, host, port, unit)


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
