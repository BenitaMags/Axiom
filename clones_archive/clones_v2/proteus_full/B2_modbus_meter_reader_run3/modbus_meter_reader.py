"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _modbustk_client_shim(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient API to modbus_tk's TcpMaster/execute API.

    pymodbus's `client.read_holding_registers(address, count, unit=...)` returns a
    response object exposing `.isError()` and `.registers`. modbus-tk instead uses a
    `modbus_tcp.TcpMaster` connection object whose `.execute(slave, function_code,
    address, count)` method either returns a plain tuple of register values or raises
    a `modbus_tk.exceptions.ModbusError` (or other modbus_tk exception) on failure.

    This shim constructs and opens a `TcpMaster`, and returns a lightweight object
    whose `read_holding_registers(address, count, unit=...)` method calls
    `master.execute(...)`, catches modbus_tk exceptions to record an error, and wraps
    the result (or the error) in a small response object exposing `.isError()` and
    `.registers`, so `MeterReader.read_measurement()` can keep calling the
    pymodbus-style API unmodified.
    """
    import modbus_tk.defines as cst
    import modbus_tk.exceptions
    from modbus_tk import modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.open()

    class _Response:
        def __init__(self, registers=None, error=False):
            self.registers = list(registers) if registers is not None else []
            self._error = error

        def isError(self):
            return self._error

    class _ClientShim:
        def read_holding_registers(self, address, count, unit=1):
            try:
                values = master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
                return _Response(registers=values, error=False)
            except modbus_tk.exceptions.ModbusError:
                return _Response(error=True)

    return _ClientShim()


def _default_client(host: str, port: int = 502):

    client = _modbustk_client_shim(host, port=port)
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self.client.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
