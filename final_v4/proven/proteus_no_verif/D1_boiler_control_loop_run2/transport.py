"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `modbus-tk` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from modbus_adapter import build_tcp_client


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Bridge modbus-tk's ``Master`` API to the pymodbus-shaped client interface
    used by :class:`ModbusIO`.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()`` plus
    ``read_holding_registers(address, count, unit=...)`` and
    ``write_register(address, value, unit=...)`` calls that return response
    objects with ``.isError()`` and (for reads) ``.registers``.

    modbus-tk instead exposes a ``modbus_tcp.TcpMaster`` whose transport is
    opened at construction/``open()`` time (there is no separate ``connect()``
    step), and whose only I/O primitive is ``execute(slave, function_code,
    address, count, ...)`` using function codes from ``modbus_tk.defines``.
    A successful read returns a plain tuple of register values (not an object
    with ``.registers``); a failed request raises a ``modbus_tk.modbus.ModbusError``
    (or a transport-level ``socket.error``/``socket.timeout``) instead of
    returning an object whose ``.isError()`` is True.

    This wrapper opens the TcpMaster eagerly (mirroring pymodbus's eager
    ``connect()``), makes ``connect()`` a no-op for callers that still invoke
    it, and translates each ``execute()`` call into a small result object that
    reproduces the ``.isError()``/``.registers`` contract the call sites rely
    on, by catching modbus-tk's exceptions and recording them rather than
    letting a different exception type escape unexpectedly.
    """
    return build_tcp_client(host, port)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_tcp_client_wrapper(host, port=port)
    client.connect()
    return client


class ModbusIO:
    _TEMPERATURE_SCALE = 10.0   # deci-degrees C -> C
    _PRESSURE_SCALE = 100.0     # centibar -> bar
    _VALVE_COUNTS_PER_PERCENT = 40.95  # 0-100% -> 0-4095
    _VALVE_MIN_PERCENT = 0.0
    _VALVE_MAX_PERCENT = 100.0

    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def _read_scaled(self, address: int, scale: float) -> float:
        return self._read1(address) / scale

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read_scaled(address, self._TEMPERATURE_SCALE)

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read_scaled(address, self._PRESSURE_SCALE)

    @classmethod
    def _percent_to_counts(cls, percent: float) -> int:
        clamped = max(cls._VALVE_MIN_PERCENT, min(cls._VALVE_MAX_PERCENT, percent))
        return int(round(clamped * cls._VALVE_COUNTS_PER_PERCENT))

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = self._percent_to_counts(percent)
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
