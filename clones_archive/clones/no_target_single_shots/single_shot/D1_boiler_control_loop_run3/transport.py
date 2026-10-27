# CHOSEN-TARGET: pyModbusTCP
"""transport -- Modbus field I/O for the boiler supervisor.


read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True)
    return client


class _PyModbusTcpAdapter:
    """Adapter that gives a pyModbusTCP client a pymodbus-like read/write API.

    Wraps a `pyModbusTCP.client.ModbusClient` so that callers can use the same
    `read_holding_registers` / `write_register` shaped calls (returning objects
    with `.isError()` / `.registers`) that the rest of this module expects.
    """

    class _Result:
        def __init__(self, registers=None, failed=False):
            self.registers = registers if registers is not None else []
            self._failed = failed

        def isError(self):
            return self._failed

    def __init__(self, raw_client):
        self._raw = raw_client

    def read_holding_registers(self, address, count, unit=1):
        try:
            self._raw.unit_id = unit
        except Exception:
            pass
        values = self._raw.read_holding_registers(address, count)
        if values is None:
            return self._Result(failed=True)
        return self._Result(registers=list(values), failed=False)

    def write_register(self, address, value, unit=1):
        try:
            self._raw.unit_id = unit
        except Exception:
            pass
        ok = self._raw.write_single_register(address, value)
        return self._Result(failed=not bool(ok))


def _wrap_client(raw_client):
    # If the injected client already speaks the pymodbus-like protocol
    # (has isError-returning calls), use it directly; otherwise adapt it.
    if hasattr(raw_client, "read_holding_registers") and hasattr(raw_client, "write_register"):
        try:
            probe = raw_client.read_holding_registers.__self__
        except AttributeError:
            probe = None
        # Heuristic: pymodbus-style clients accept a `unit=` kwarg and return
        # objects with `.isError()`; pyModbusTCP clients do not accept `unit=`.
        return raw_client
    return _PyModbusTcpAdapter(raw_client)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if client is not None:
            self.client = client
        else:
            raw = _default_client(host)
            self.client = _PyModbusTcpAdapter(raw)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        raw_value = self._read1(address)
        return raw_value / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        raw_value = self._read1(address)
        return raw_value / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        bounded_percent = max(0.0, min(100.0, percent))
        counts = int(round(bounded_percent * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
