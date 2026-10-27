"""modbus_response -- shared pymodbus-style response value object.

pymodbus's read/write calls return an object exposing `.isError()` and,
for reads, `.registers` (a list of ints). This module provides a small
reusable holder with that same shape, used by transport shims that need to
translate a different client library's success/failure signalling into it.
"""


class ModbusResponse:
    """pymodbus-style response holder.

    Packages either a successful result or a translated failure into the
    `.isError()` / `.registers` shape pymodbus produces, so call sites
    written against pymodbus's return-object style keep working unchanged.
    """

    def __init__(self, registers=None, is_error=False):
        self.registers = registers if registers is not None else []
        self._is_error = is_error

    def isError(self):
        return self._is_error
