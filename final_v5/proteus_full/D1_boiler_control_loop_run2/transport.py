"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from modbus_tk_facade import build_modbus_tk_client


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
    return build_modbus_tk_client(host, port)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_tk_client_facade(host, port=port)
    client.connect()
    return client


class _RegisterScale:
    """Pairs a scale factor with the conversion it performs on a raw register."""

    def __init__(self, factor: float):
        self.factor = factor

    def to_engineering(self, raw: int) -> float:
        return raw / self.factor


_TEMPERATURE_SCALE = _RegisterScale(10.0)   # deci-degrees C -> C
_PRESSURE_SCALE = _RegisterScale(100.0)     # centibar -> bar

_VALVE_COUNTS_PER_PERCENT = 40.95  # 0-100% -> 0-4095
_VALVE_MIN_PERCENT = 0.0
_VALVE_MAX_PERCENT = 100.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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
        return _TEMPERATURE_SCALE.to_engineering(self._read1(address))

    def read_pressure(self, address: int = 0x0001) -> float:
        return _PRESSURE_SCALE.to_engineering(self._read1(address))

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        clamped = _clamp(percent, _VALVE_MIN_PERCENT, _VALVE_MAX_PERCENT)
        counts = int(round(clamped * _VALVE_COUNTS_PER_PERCENT))
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
