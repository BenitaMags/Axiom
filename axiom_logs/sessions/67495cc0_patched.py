"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_tk_client_facade(host: str, port: int = 502):
    """Bridge modbus-tk's Master/execute API to the pymodbus client shape
    used by ModbusIO below.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()`` plus
    ``read_holding_registers(address, count, unit=...)`` and
    ``write_register(address, value, unit=...)`` calls that return a
    response object with ``.isError()`` and (for reads) ``.registers``.

    modbus-tk instead exposes a single ``TcpMaster`` whose ``execute(unit,
    function_code, address, count_or_output_value)`` either returns a tuple
    of register values (for reads) or raises a ``modbus_tk`` exception
    (subclasses of ``modbus_tk.exceptions.ModbusError``) or a socket error
    on any failure -- there is no error-carrying return value.

    This facade constructs the ``TcpMaster``, opens it on ``connect()``,
    and re-implements ``read_holding_registers``/``write_register`` on top
    of ``execute()``, catching modbus-tk's protocol exceptions as well as
    the transport-level ``socket`` errors/timeouts and malformed-response
    ``struct.error`` it can raise, and converting any of them into a small
    response object whose ``.isError()`` returns True -- exactly mirroring
    the pymodbus response shape the call sites already inspect
    (``rr.isError()``, ``rr.registers[0]``, ``wr.isError()``), without
    swallowing anything: unexpected exceptions from modbus-tk still
    propagate.
    """
    import socket
    import struct

    import modbus_tk.defines as cst
    import modbus_tk.exceptions as mtk_exceptions
    import modbus_tk.modbus_tcp as modbus_tcp

    _CAUGHT_EXCEPTIONS = (
        mtk_exceptions.ModbusError,
        mtk_exceptions.ModbusInvalidResponseError,
        mtk_exceptions.ModbusInvalidRequestError,
        mtk_exceptions.ModbusFunctionNotSupportedError,
        mtk_exceptions.OutOfModbusBlockError,
        mtk_exceptions.OverlapModbusBlockError,
        mtk_exceptions.InvalidArgumentError,
        mtk_exceptions.MissingKeyError,
        mtk_exceptions.DuplicatedKeyError,
        mtk_exceptions.InvalidModbusBlockError,
        socket.error,
        socket.timeout,
        struct.error,
    )

    class _AxiomModbusResponse:
        def __init__(self, registers=None, error=False):
            self.registers = registers if registers is not None else []
            self._error = error

        def isError(self):
            return self._error

    class _AxiomModbusTkClient:
        def __init__(self, host, port):
            import modbus_tk.modbus_tcp as modbus_tcp  # connector: bound to this scope
            self._master = modbus_tcp.TcpMaster(host=host, port=port)

        def connect(self):
            try:
                self._master.open()
                return True
            except _CAUGHT_EXCEPTIONS:
                return False

        def read_holding_registers(self, address, count=1, unit=1):
            import modbus_tk.defines as cst  # connector: bound to this scope
            try:
                values = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
                return _AxiomModbusResponse(registers=list(values), error=False)
            except _CAUGHT_EXCEPTIONS:
                return _AxiomModbusResponse(error=True)

        def write_register(self, address, value, unit=1):
            import modbus_tk.defines as cst  # connector: bound to this scope
            try:
                self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
                return _AxiomModbusResponse(registers=[value], error=False)
            except _CAUGHT_EXCEPTIONS:
                return _AxiomModbusResponse(error=True)

    return _AxiomModbusTkClient(host, port)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_tk_client_facade(host, port=port)
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
