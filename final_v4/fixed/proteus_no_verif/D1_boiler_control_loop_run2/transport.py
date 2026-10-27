"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from modbus_client_adapter import build_modbus_tk_client


def _axiom_modbus_client_adapter(host: str, port: int = 502):
    """Bridge modbus-tk's TcpMaster to the pymodbus ModbusTcpClient API
    that the call sites in this module (`_read1`, `write_valve`, and
    `_default_client` below) were written against.

    pymodbus's client methods (`connect`, `read_holding_registers`,
    `write_register`) never raise for protocol-level failures: `connect()`
    returns True/False, and the read/write calls return a response object
    exposing `.isError()` (bool) and, for reads, `.registers` (a list of
    ints). Callers branch on `.isError()` themselves.

    modbus-tk's `TcpMaster.execute()` instead returns a plain tuple of
    register values on success and *raises* on failure -- either a
    `modbus_tk.exceptions.ModbusError` subclass (protocol errors: illegal
    address/function, slave device failure, malformed/invalid response) or
    an `OSError` (connection refused, broken pipe, socket timeout).

    This adapter calls `execute()` with the correct function code and
    slave/unit id, catches that same set of exceptions -- matching the
    range of failures pymodbus reported through `.isError()` at this call
    site, no broader -- and converts them into a small response object with
    `.isError()` True and empty `.registers`, so `_read1` and `write_valve`
    (which already raise their own `IOError` when `.isError()` is True)
    keep working completely unmodified.
    """
    return build_modbus_tk_client(host, port)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_client_adapter(host, port)
    client.connect()
    return client


class ModbusIO:
    _TEMPERATURE_SCALE = 10.0  # deci-degrees C -> C
    _PRESSURE_SCALE = 100.0  # centibar -> bar
    _VALVE_COUNTS_PER_PERCENT = 40.95  # 0-100% -> 0-4095

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

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        clamped = max(0.0, min(100.0, percent))
        counts = int(round(clamped * self._VALVE_COUNTS_PER_PERCENT))
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
