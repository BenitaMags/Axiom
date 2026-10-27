"""modbus_shim -- translation layer between modbus-tk and the pymodbus-shaped
client API expected by ``transport.ModbusIO``.

This module exists so the "different Modbus library" glue can be reused or
tested independently of the transport module itself.
"""


class AxiomModbusResponse:
    """pymodbus-shaped response object.

    pymodbus read/write calls return an object exposing ``.isError()`` and
    (for reads) ``.registers``. modbus-tk's ``execute()`` returns a plain
    tuple of values on success and raises on failure, so this class is the
    translation target: callers keep using ``rr.isError()`` /
    ``rr.registers[0]`` unmodified regardless of which library actually
    performed the I/O.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


def _is_transport_failure(exc: BaseException) -> bool:
    """True for the exact exception types the original code caught.

    modbus-tk raises ``modbus_tk.exceptions.ModbusError`` (and siblings) for
    protocol errors, plus plain ``socket.error``/``socket.timeout`` for
    transport failures. This helper centralizes the isinstance check that
    was previously repeated at each call site.
    """
    import socket
    import modbus_tk.exceptions as modbus_tk_exceptions

    return isinstance(exc, (modbus_tk_exceptions.ModbusError, socket.error, socket.timeout))


class AxiomModbusClient:
    """Adapts a modbus_tk ``TcpMaster`` to the pymodbus client method names.

    modbus-tk raises exceptions (``modbus_tk.exceptions.ModbusError`` and
    its siblings for protocol errors, plus plain ``socket`` errors for
    transport failures such as connection refused/timeout) where pymodbus
    would have returned an error-flagged response object. Every method here
    catches exactly those failure modes and re-encodes them as
    ``AxiomModbusResponse(error=True)`` so ``ModbusIO`` can keep calling
    ``rr.isError()`` the way it did against pymodbus. Anything not part of
    that failure surface is left to propagate.
    """

    def __init__(self, master):
        self._master = master

    def connect(self) -> bool:
        try:
            self._master.open()
            return True
        except Exception as exc:
            if _is_transport_failure(exc):
                return False
            raise

    def read_holding_registers(self, address: int, count: int, unit: int = 1) -> "AxiomModbusResponse":
        import modbus_tk.defines as cst

        try:
            values = self._master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count)
            return AxiomModbusResponse(registers=list(values), error=False)
        except Exception as exc:
            if _is_transport_failure(exc):
                return AxiomModbusResponse(registers=[], error=True)
            raise

    def write_register(self, address: int, value: int, unit: int = 1) -> "AxiomModbusResponse":
        import modbus_tk.defines as cst

        try:
            self._master.execute(
                unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
            return AxiomModbusResponse(registers=[value], error=False)
        except Exception as exc:
            if _is_transport_failure(exc):
                return AxiomModbusResponse(registers=[], error=True)
            raise

    def close(self) -> None:
        try:
            self._master.close()
        except Exception as exc:
            if _is_transport_failure(exc):
                return
            raise
