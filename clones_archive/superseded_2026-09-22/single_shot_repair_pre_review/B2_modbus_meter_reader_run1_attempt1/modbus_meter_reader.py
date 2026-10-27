# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True)
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            if hasattr(self.client, "unit_id"):
                # pyModbusTCP.client.ModbusClient supports setting unit id
                try:
                    self.client.unit_id = self.unit
                except Exception:
                    pass
            registers = self.client.read_holding_registers(address, count)
            if registers is None:
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, registers, scale)
        return record
