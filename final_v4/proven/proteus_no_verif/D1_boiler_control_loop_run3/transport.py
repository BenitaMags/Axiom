"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from modbus_adapter import build_modbus_tk_client


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Build a modbus-tk based stand-in for pymodbus's ``ModbusTcpClient``.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()`` (returns True/False),
    and ``read_holding_registers(address, count, unit=...)`` /
    ``write_register(address, value, unit=...)`` which both return a response
    object with ``.isError()`` (True on failure) and ``.registers`` (a list
    of the values read/written).

    modbus-tk's ``TcpMaster`` instead uses ``open()`` (raises on failure,
    returns None on success) and a single ``execute(unit, function_code,
    address, count_or_kwargs...)`` call that returns a plain tuple of
    register values on success, and RAISES (``modbus_tk.exceptions.
    ModbusError`` and friends, or a socket/OSError for transport failures)
    on failure -- it never returns None or an error code.

    This wrapper translates that: it catches the exception classes
    modbus-tk raises for protocol errors (``ModbusError`` subclasses) and
    transport errors (``OSError``, which covers connection refused/reset and
    timeouts) and converts them into the same True/False and
    isError()/registers shapes pymodbus used, so callers written against
    pymodbus's contract keep working unmodified.
    """
    return build_modbus_tk_client(host, port)


def _default_client(host: str, port: int = 502):

    return _axiom_modbus_client_wrapper(host, port)


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
