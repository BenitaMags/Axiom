"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_tcp_client_factory(host: str, port: int = 502):
    """Build a modbus-tk based stand-in for pymodbus's ModbusTcpClient.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()`` plus
    ``read_holding_registers(address, count, unit=...)`` /
    ``write_register(address, value, unit=...)``. Those calls return response
    objects with an ``.isError()`` method and, for reads, a ``.registers``
    list on success.

    modbus-tk instead exposes a ``modbus_tk.modbus_tcp.TcpMaster`` object.
    Connecting is done via ``master.open()`` rather than ``connect()``, and
    reads/writes are performed through a single ``master.execute(slave,
    function_code, address, ...)`` call that returns a plain tuple of
    register values on success and raises a ``modbus_tk.exceptions
    .ModbusError`` on failure -- there is no error-flagged response object.

    This factory constructs and opens the ``TcpMaster``, then returns a shim
    object that re-exposes ``connect()`` (a no-op, since the master is
    already open), ``read_holding_registers(...)`` and
    ``write_register(...)``. Internally the shim calls ``master.execute``
    with the appropriate modbus-tk function code and translates the
    tuple-or-exception result back into a small ``_Result`` object carrying
    ``.isError()``/``.registers``, so that ``ModbusIO._read1``/
    ``write_valve`` keep working unmodified against the pymodbus-shaped
    response API.
    """
    import modbus_tk.modbus_tcp as modbus_tcp
    import modbus_tk.defines as cst
    import modbus_tk.exceptions as mtk_exceptions

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.open()

    class _Result:
        """Mimics a pymodbus response object's .isError()/.registers shape."""

        def __init__(self, registers=None, error=None):
            self.registers = registers
            self._error = error

        def isError(self):
            return self._error is not None

    class _TcpMasterShim:
        def __init__(self, master):
            self._master = master

        def connect(self):
            # modbus-tk's master is opened eagerly by the factory; pymodbus's
            # connect() is idempotent/safe to call again, so a no-op keeps
            # existing `client.connect()` call sites working unmodified.
            return True

        def read_holding_registers(self, address, count, unit=1):
            try:
                registers = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except mtk_exceptions.ModbusError as exc:
                # pymodbus reported this failure via an error-flagged
                # response object rather than an exception, so we translate
                # modbus-tk's exception into that same status-object shape.
                return _Result(error=exc)
            return _Result(registers=list(registers))

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
                )
            except mtk_exceptions.ModbusError as exc:
                return _Result(error=exc)
            return _Result(registers=None)

    return _TcpMasterShim(master)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_tcp_client_factory(host, port=port)
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
