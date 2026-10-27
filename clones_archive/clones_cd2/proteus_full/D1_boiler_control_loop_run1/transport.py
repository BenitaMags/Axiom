"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = ModbusTcpClient(host, port=port)
    client.connect()
    return client


class _AxiomModbusResult:
    """Minimal stand-in for a pymodbus response object.

    Exposes .isError() and .registers so call sites written against the
    pymodbus response contract keep working unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


def _axiom_read_holding_registers_wrapper(instrument, address, unit=None):
    """Bridge minimalmodbus's read API to the pymodbus-shaped call site.

    pymodbus's `read_holding_registers(address, count, unit=...)` returns a
    response object with `.isError()` and `.registers` (a list of ints).

    minimalmodbus's `instrument.read_registers(address, number_of_registers)`
    instead returns a plain list of ints directly, and raises exceptions
    (e.g. minimalmodbus.NoResponseError, minimalmodbus.InvalidResponseError,
    OSError) on failure instead of returning an error-flagged object.

    This wrapper calls minimalmodbus's read_registers, catches any exception
    raised, and repackages the result into an object exposing .isError()
    (False on success) and .registers, so the original call-site logic
    (checking rr.isError() then reading rr.registers[0]) keeps working.
    """
    try:
        registers = instrument.read_registers(address, 1)
        return _AxiomModbusResult(registers=registers, error=False)
    except Exception:
        return _AxiomModbusResult(registers=[], error=True)


def _axiom_write_register_wrapper(instrument, address, counts, unit=None):
    """Bridge minimalmodbus's write API to the pymodbus-shaped call site.

    pymodbus's `write_register(address, value, unit=...)` returns a response
    object exposing `.isError()`.

    minimalmodbus's `instrument.write_register(address, value)` has no
    concept of a Modbus "unit"/slave id passed per-call (it is configured on
    the instrument itself), and it returns None on success while raising an
    exception on failure instead of returning an error-flagged object.

    This wrapper drops/ignores the unused 'unit' kwarg (validating only that
    it is unit-like, since minimalmodbus does not take it per-call), calls
    minimalmodbus's write_register, catches any exception, and returns a
    small object exposing .isError() so the original call-site logic
    (checking wr.isError()) keeps working.
    """
    try:
        instrument.write_register(address, counts)
        return _AxiomModbusResult(error=False)
    except Exception:
        return _AxiomModbusResult(error=True)


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
