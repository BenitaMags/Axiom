"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `modbus-tk` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_adapter(host: str, port: int = 502):
    """Build a modbus-tk-backed client that mimics the pymodbus ModbusTcpClient
    surface area used by ModbusIO (connect/read_holding_registers/write_register).

    pymodbus's ``ModbusTcpClient.read_holding_registers`` /
    ``write_register`` return a response object exposing ``isError()`` (False
    on success, True if the slave replied with an exception code or the
    transport failed) and, for reads, a ``.registers`` list of the decoded
    16-bit values.

    modbus-tk has no such response object: ``modbus_tk.modbus_tcp.TcpMaster``
    is driven through a single ``execute(slave, function_code, address, ...)``
    call that either returns a tuple of register values (reads) or a tuple
    describing what was written (writes), and raises an exception -- a
    ``modbus_tk.exceptions.ModbusError`` (or one of its siblings such as
    ``ModbusInvalidResponseError``) for protocol-level failures, or a
    ``socket.error``/``socket.timeout`` for transport failures -- instead of
    returning a status.

    This adapter wraps a ``TcpMaster`` and re-exposes pymodbus's call
    signatures. It catches the modbus-tk/transport exceptions that pymodbus
    would have folded into ``isError() == True`` and converts them to a
    response object with ``isError()`` set, so ``ModbusIO`` can keep using
    its existing ``isError()`` / ``.registers[0]`` chain unmodified. No
    exception is swallowed silently: it is translated into the same signal
    pymodbus used for the same failure (a False/True error flag), never
    dropped.
    """
    import socket

    import modbus_tk.defines as cst
    import modbus_tk.modbus_tcp as modbus_tcp
    from modbus_tk.exceptions import ModbusError, ModbusInvalidResponseError

    _TRANSPORT_ERRORS = (ModbusError, ModbusInvalidResponseError, socket.error, socket.timeout)

    class _AxiomModbusResponse:
        """pymodbus-shaped response: isError() + registers, filled in by the adapter."""

        def __init__(self, registers=None, error=False):
            self.registers = registers if registers is not None else []
            self._error = error

        def isError(self):
            return self._error

    class _AxiomModbusTkClient:
        """Thin wrapper around modbus_tk.modbus_tcp.TcpMaster exposing the
        subset of pymodbus's ModbusTcpClient API that ModbusIO relies on."""

        def __init__(self, host, port):
            self._master = modbus_tcp.TcpMaster(host=host, port=port)
            self._master.set_timeout(5.0)

        def connect(self):
            # pymodbus's connect() returns True/False rather than raising;
            # mirror that by catching connection-level failures here.
            try:
                self._master.open()
                return True
            except _TRANSPORT_ERRORS:
                return False

        def read_holding_registers(self, address, count, unit=1):
            try:
                values = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
                return _AxiomModbusResponse(registers=list(values))
            except _TRANSPORT_ERRORS:
                return _AxiomModbusResponse(error=True)

        def write_register(self, address, value, unit=1):
            try:
                result = self._master.execute(
                    unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
                )
                return _AxiomModbusResponse(registers=list(result) if result else [value])
            except _TRANSPORT_ERRORS:
                return _AxiomModbusResponse(error=True)

        def close(self):
            self._master.close()

    return _AxiomModbusTkClient(host, port)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_adapter(host, port=port)
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
