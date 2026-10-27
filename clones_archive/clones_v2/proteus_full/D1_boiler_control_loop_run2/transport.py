"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

class _ModbusResponse:
    """Pymodbus-like response wrapper.

    pymodbus calls return a response object exposing ``.isError()`` and,
    for reads, a ``.registers`` list of the returned register values.
    modbus-tk's ``TcpMaster.execute(...)`` instead returns a plain tuple of
    register values on success and raises a ``modbus_tk.modbus.ModbusError``
    (or socket/exception) on failure. This class re-packages either outcome
    into an object with the same ``.isError()`` / ``.registers`` shape so
    downstream code (``rr.isError()``, ``rr.registers[0]``, ``wr.isError()``)
    keeps working unchanged.
    """

    def __init__(self, registers=None, error=False):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _PymodbusCompatClient:
    """Shim exposing a pymodbus-like client API on top of modbus-tk.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()``/``close()`` for the
    connection lifecycle and ``read_holding_registers(address, count,
    unit=...)`` / ``write_register(address, value, unit=...)`` methods that
    return response objects with ``.isError()`` / ``.registers``.

    modbus-tk instead centers on ``modbus_tk.modbus_tcp.TcpMaster``, whose
    lifecycle methods are ``open()``/``close()`` and whose I/O is done via a
    single ``execute(slave, function_code, address, count_or_output_value)``
    call that returns a plain tuple (or raises on error).

    This wrapper stores a ``TcpMaster`` instance and translates each
    pymodbus-style call into the equivalent ``execute(...)`` call, catching
    modbus-tk exceptions and converting the tuple/exception outcome into a
    ``_ModbusResponse`` so existing call sites do not need to change their
    error-handling or result-access patterns.
    """

    def __init__(self, host: str, port: int = 502):
        import modbus_tk.modbus_tcp as modbus_tcp

        self._master = modbus_tcp.TcpMaster(host=host, port=port)

    def connect(self):
        self._master.open()
        return True

    def close(self):
        self._master.close()

    def read_holding_registers(self, address, count, unit: int = 1):
        import modbus_tk.defines as cst
        import modbus_tk.modbus as modbus_exceptions

        try:
            result = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except (modbus_exceptions.ModbusError, Exception):
            return _ModbusResponse(error=True)
        return _ModbusResponse(registers=list(result))

    def write_register(self, address, value, unit: int = 1):

        try:
            self._master.execute(
                unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
        except (modbus_exceptions.ModbusError, Exception):
            return _ModbusResponse(error=True)
        return _ModbusResponse(registers=[value])


def _pymodbus_compat_client(host: str, port: int = 502):
    """Build a pymodbus-compatible client backed by modbus-tk.

    pymodbus code constructs ``ModbusTcpClient(host, port=port)`` and then
    calls ``.connect()`` on it. modbus-tk instead requires building a
    ``modbus_tk.modbus_tcp.TcpMaster`` and calling ``.open()``. This helper
    hides that difference behind the same construction + ``connect()``
    pattern used by the original code.
    """

    client = _PymodbusCompatClient(host, port=port)
    client.connect()
    return client


def _default_client(host: str, port: int = 502):

    client = _pymodbus_compat_client(host, port=port)
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
