"""Helper module bridging modbus-tk's Master/execute API to the
pymodbus-shaped client interface expected by transport.ModbusIO.

Kept separate from transport.py so the modbus-tk dependency stays an
implementation detail imported lazily by the factory function below.
"""


def _caught_exceptions():
    import socket
    import struct

    import modbus_tk.exceptions as mtk_exceptions

    return (
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
        self._caught = _caught_exceptions()

    def connect(self):
        try:
            self._master.open()
            return True
        except self._caught:
            return False

    def read_holding_registers(self, address, count=1, unit=1):
        import modbus_tk.defines as cst  # connector: bound to this scope
        try:
            values = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
            return _AxiomModbusResponse(registers=list(values), error=False)
        except self._caught:
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
        except self._caught:
            return _AxiomModbusResponse(error=True)


def build_modbus_tk_client(host: str, port: int = 502):
    import modbus_tk.defines as cst  # noqa: F401  (import kept deferred/local)
    import modbus_tk.exceptions as mtk_exceptions  # noqa: F401
    import modbus_tk.modbus_tcp as modbus_tcp  # noqa: F401

    return _AxiomModbusTkClient(host, port)
