# CHOSEN-TARGET: pyModbusTCP
"""transport -- Modbus field I/O for the boiler supervisor.


read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True)
    return client


class _ReadResult:
    """Adapter object mimicking the pymodbus read-response API used by ModbusIO."""

    def __init__(self, registers):
        self.registers = registers

    def isError(self):
        return self.registers is None


class _WriteResult:
    """Adapter object mimicking the pymodbus write-response API used by ModbusIO."""

    def __init__(self, ok):
        self._ok = ok

    def isError(self):
        return not self._ok


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read_holding_registers(self, address: int, count: int):
        """Call the underlying client's holding-register read, tolerating
        either a pyModbusTCP-style client (read_holding_registers(addr, count)
        returning a list or None) or an injected pymodbus-style client
        (read_holding_registers(address, count, unit=...) returning an object
        with .isError()/.registers), and normalize the result to a
        `_ReadResult`.
        """
        client = self.client

        # Native pyModbusTCP client.
        if hasattr(client, "unit_id"):
            client.unit_id = self.unit
            regs = client.read_holding_registers(address, count)
            return _ReadResult(regs)

        # Fallback: injected client with a pymodbus-like API (used by tests).
        try:
            rr = client.read_holding_registers(address, count, unit=self.unit)
        except TypeError:
            rr = client.read_holding_registers(address, count)
        return rr

    def _write_holding_register(self, address: int, value: int):
        client = self.client

        # Native pyModbusTCP client.
        if hasattr(client, "unit_id"):
            client.unit_id = self.unit
            ok = client.write_single_register(address, value)
            return _WriteResult(ok)

        # Fallback: injected client with a pymodbus-like API (used by tests).
        try:
            wr = client.write_register(address, value, unit=self.unit)
        except TypeError:
            wr = client.write_register(address, value)
        return wr

    def _read1(self, address: str) -> int:
        rr = self._read_holding_registers(address, 1)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self._write_holding_register(address, counts)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
