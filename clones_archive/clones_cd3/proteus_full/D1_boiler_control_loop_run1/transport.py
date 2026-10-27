"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_connect_noop_wrapper(client):
    """Bridge for the pymodbus `client.connect()` call.

    pymodbus's `ModbusTcpClient` (or serial client) requires an explicit
    `.connect()` call before any read/write can be performed, and that
    call returns a bool indicating success. minimalmodbus's `Instrument`
    has no such concept: the underlying serial port is opened as part of
    constructing the `Instrument` object itself, so there is nothing left
    to "connect". This wrapper simply performs no action and returns
    None, preserving the call site's shape without requiring pymodbus's
    connect semantics. Note that this only papers over the `.connect()`
    call itself -- it does not make `client` a valid, already-open
    minimalmodbus `Instrument`; that object still has to be constructed
    against a real serial port elsewhere.
    """
    return None


class _AxiomModbusResult:
    """Fake response object mimicking pymodbus's read/write results.

    pymodbus read/write calls return a response object exposing
    `.isError()` (bool) and, for reads, `.registers` (list of ints).
    minimalmodbus instead returns the register value(s) directly on
    success and raises an exception (e.g. IOError, ValueError) on
    failure. This helper packages either a successful minimalmodbus
    result or the exception raised while obtaining it into an object
    with the same `.isError()` / `.registers` shape, so existing
    call sites can keep using the pymodbus-style error-check pattern
    without modification.
    """

    def __init__(self, registers=None, error=None):
        self._error = error
        self.registers = registers if registers is not None else []

    def isError(self):
        return self._error is not None


def _axiom_read_holding_registers_wrapper(client, address, unit=None):
    """Bridge for pymodbus's `client.read_holding_registers(address, 1, unit=...)`.

    pymodbus returns a response object with `.isError()` and
    `.registers`. minimalmodbus's `Instrument.read_registers(address,
    number_of_registers, functioncode=3)` returns a plain list of
    register values on success and raises an exception on failure. The
    `unit` kwarg from pymodbus (the slave/unit id) has no equivalent
    per-call parameter in minimalmodbus -- the slave address is fixed on
    the `Instrument` itself -- so it is accepted here only for interface
    compatibility and otherwise ignored. This wrapper calls
    minimalmodbus's `read_registers` with function code 3 (read holding
    registers, matching pymodbus's `read_holding_registers`), catches
    any exception, and returns an `_AxiomModbusResult` so the caller's
    existing `.isError()` / `.registers` usage keeps working.
    """
    try:
        registers = client.read_registers(address, 1, functioncode=3)
        return _AxiomModbusResult(registers=registers)
    except Exception as exc:  # noqa: BLE001 - translate any minimalmodbus failure
        return _AxiomModbusResult(error=exc)


def _axiom_write_register_wrapper(client, address, counts, unit=None):
    """Bridge for pymodbus's `client.write_register(address, counts, unit=...)`.

    pymodbus returns a response object with `.isError()`. minimalmodbus's
    `Instrument.write_register(address, value, functioncode=16)` returns
    None on success and raises an exception on failure. As with reads,
    the pymodbus `unit` kwarg has no per-call minimalmodbus equivalent
    (the slave address lives on the `Instrument`), so it is accepted for
    interface compatibility only and ignored. This wrapper calls
    minimalmodbus's `write_register` with function code 16 (preset
    multiple/holding register write, matching pymodbus's
    `write_register`), catches any exception, and returns an
    `_AxiomModbusResult` so the caller's existing `.isError()` usage
    keeps working.
    """
    try:
        client.write_register(address, counts, functioncode=16)
        return _AxiomModbusResult()
    except Exception as exc:  # noqa: BLE001 - translate any minimalmodbus failure
        return _AxiomModbusResult(error=exc)


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = ModbusTcpClient(host, port=port)
    _axiom_modbus_connect_noop_wrapper(client)
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
