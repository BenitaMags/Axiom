# CHOSEN-TARGET: pyModbusTCP
"""transport -- Modbus field I/O for the boiler supervisor.


read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True)
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit) \
            if _accepts_unit_kwarg(self.client.read_holding_registers) \
            else self.client.read_holding_registers(address, 1)

        if rr is None:
            raise IOError(f"Modbus read failed @ {address:#06x}")

        # Support both plain-list style results (e.g. pyModbusTCP: [value, ...])
        # and object-style results (e.g. pymodbus-like: .isError()/.registers).
        if hasattr(rr, "isError"):
            if rr.isError():
                raise IOError(f"Modbus read failed @ {address:#06x}")
            return rr.registers[0]
        return rr[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095

        if _accepts_unit_kwarg(self.client.write_single_register) if hasattr(
            self.client, "write_single_register"
        ) else _accepts_unit_kwarg(self.client.write_register):
            if hasattr(self.client, "write_single_register"):
                wr = self.client.write_single_register(address, counts, unit=self.unit)
            else:
                wr = self.client.write_register(address, counts, unit=self.unit)
        else:
            if hasattr(self.client, "write_single_register"):
                wr = self.client.write_single_register(address, counts)
            else:
                wr = self.client.write_register(address, counts)

        # Support both boolean-style results (pyModbusTCP: True/False)
        # and object-style results (pymodbus-like: .isError()).
        if hasattr(wr, "isError"):
            if wr.isError():
                raise IOError(f"Modbus write failed @ {address:#06x}")
        else:
            if not wr:
                raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts


def _accepts_unit_kwarg(func) -> bool:
    """Best-effort check whether `func` accepts a `unit=` keyword argument."""
    try:
        import inspect

        sig = inspect.signature(func)
        if "unit" in sig.parameters:
            return True
        return any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
    except (TypeError, ValueError):
        return False
