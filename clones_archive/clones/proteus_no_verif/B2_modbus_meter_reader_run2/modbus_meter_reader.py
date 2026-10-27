"""modbus_meter_reader -- read a power meter's holding registers into a record.

The Modbus client is the substitution target: `pymodbus` -> `minimalmodbus`. Their
read APIs and result objects differ (pymodbus returns a response object with
`.registers`/`.isError()`; minimalmodbus returns raw register lists and raises), so a
clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode

import modbus_tk.defines as cst


class _AxiomModbusTkResult:
    """Adapter result object mimicking pymodbus's response shape.

    modbus-tk's `master.execute(...)` returns a plain tuple/list of register
    values on success and raises an exception on failure, whereas pymodbus
    callers expect a response object exposing `.isError()` (bool) and
    `.registers` (the list of values). This tiny wrapper stores the raw
    values and always reports `isError() == False`, since any failure from
    modbus-tk surfaces as a raised exception before this object is
    constructed.
    """

    def __init__(self, registers):
        self.registers = list(registers)

    def isError(self):
        return False


class _AxiomModbusTkClientWrapper:
    """Bridges modbus-tk's TcpMaster to the pymodbus client API used here.

    pymodbus's `ModbusTcpClient` exposes `.connect()` and
    `.read_holding_registers(address, count, unit=...)`, the latter
    returning a response object with `.isError()`/`.registers`. modbus-tk's
    `TcpMaster` instead exposes `.open()`/`.close()` and requires building a
    query via `master.execute(unit, cst.READ_HOLDING_REGISTERS, address,
    count)`, which returns a plain tuple of register values (or raises on
    error). This wrapper maps `.connect()` to `.open()` and wraps the
    `.execute()` result in `_AxiomModbusTkResult` so `MeterReader` can keep
    calling the pymodbus-style API unmodified.
    """

    def __init__(self, master):
        self._master = master

    def connect(self):
        return self._master.open()

    def close(self):
        return self._master.close()

    def read_holding_registers(self, address, count, unit=1):
        registers = self._master.execute(
            unit, cst.READ_HOLDING_REGISTERS, address, count
        )
        return _AxiomModbusTkResult(registers)


def _axiom_modbustk_client_wrapper(host: str, port: int = 502):
    """Build a modbus-tk TcpMaster and adapt it to the pymodbus client API.

    See `_AxiomModbusTkClientWrapper` and `_AxiomModbusTkResult` for the
    details of the shape translation this performs.
    """
    from modbus_tk.modbus_tcp import TcpMaster

    master = TcpMaster(host=host, port=port)
    return _AxiomModbusTkClientWrapper(master)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbustk_client_wrapper(host, port=port)
    client.connect()
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
