"""modbus_response -- shared response shape for pymodbus-like adapters."""


class ModbusResponse:
    """Base pymodbus-shaped response object.

    Exposes `.registers` (a list of ints) and `.isError()`, matching what
    pymodbus's read/write calls return, so callers can treat any adapter's
    response uniformly regardless of the underlying client library.
    """

    def __init__(self, registers=None, error=None):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None
