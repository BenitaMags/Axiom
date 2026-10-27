"""modbus_compat -- pymodbus-shaped shim over a modbus_tk transport.

Kept separate from transport.py: this is the glue that lets ModbusIO be
written against a pymodbus-like client while actually talking modbus_tk.
"""


class _FakeModbusResponse:
    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class ModbusTkClientShim:
    """Re-exposes the pymodbus ModbusTcpClient methods ModbusIO uses,
    backed by a modbus_tk TcpMaster.

    pymodbus's ``ModbusTcpClient`` exposes ``.connect()`` plus
    ``read_holding_registers(address, count, unit=...)`` /
    ``write_register(address, value, unit=...)`` calls that return a response
    object with ``.isError()`` (bool) and ``.registers`` (list of ints). On a
    protocol failure or a transport failure (refused connection, timeout,
    malformed/short response) pymodbus's synchronous client does not raise --
    it hands back an error-flagged response object, and callers here check
    ``.isError()``.

    modbus_tk has no equivalent named methods: everything goes through
    ``TcpMaster.execute(slave, function_code, address, quantity_of_x=...,
    output_value=...)``, which returns a tuple of register values on success
    and *raises* on failure (``modbus_tk.exceptions.ModbusError`` and its
    siblings for protocol errors, plus plain ``socket.error``/
    ``socket.timeout``/``struct.error`` for transport errors, since modbus_tk
    does not wrap those in its own hierarchy).

    This shim wraps an already-opened ``TcpMaster`` (persistent, like
    pymodbus's ``connect()``) and re-exposes the two pymodbus method names
    ModbusIO calls. Each shim method calls ``execute()`` with the right
    function code, and translates any raised exception into the same
    isError()/registers response shape pymodbus would have returned for that
    failure -- it does not swallow the failure, it just reports it the way
    the caller already expects.
    """

    def __init__(self, master, read_code, write_code, transport_errors):
        self._master = master
        self._read_code = read_code
        self._write_code = write_code
        self._transport_errors = transport_errors

    def connect(self) -> bool:
        # The underlying TcpMaster is already opened by the factory; kept
        # for API parity with pymodbus's ModbusTcpClient.
        return True

    def close(self) -> None:
        self._master.close()

    def read_holding_registers(self, address, count, unit=1):
        try:
            values = self._master.execute(unit, self._read_code, address, count)
        except self._transport_errors:
            return _FakeModbusResponse(error=True)
        return _FakeModbusResponse(registers=list(values), error=False)

    def write_register(self, address, value, unit=1):
        try:
            self._master.execute(
                unit,
                self._write_code,
                address,
                output_value=value,
            )
        except self._transport_errors:
            return _FakeModbusResponse(error=True)
        return _FakeModbusResponse(registers=[value], error=False)


def build_modbus_tk_client(host: str, port: int = 502):
    """Build a pymodbus-shaped client on top of modbus_tk.

    Opens a ``TcpMaster`` once and wraps it in ``ModbusTkClientShim``.
    """
    import modbus_tk.modbus_tcp as modbus_tcp  # lazy import for testability
    import modbus_tk.defines as cst
    import modbus_tk.exceptions as mt_exceptions
    import socket
    import struct

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.open()

    transport_errors = (
        mt_exceptions.ModbusError,
        socket.error,
        socket.timeout,
        struct.error,
    )

    return ModbusTkClientShim(
        master,
        cst.READ_HOLDING_REGISTERS,
        cst.WRITE_SINGLE_REGISTER,
        transport_errors,
    )
