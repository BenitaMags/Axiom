"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

def _axiom_modbus_client_factory(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient construction/connect API to pyModbusTCP.

    pymodbus exposes a `ModbusTcpClient(host, port=port)` constructor plus a
    separate `.connect()` call that must be invoked before use. pyModbusTCP's
    `ModbusClient` is constructed with `host=`/`port=` keyword arguments and
    uses `.open()` (not `.connect()`) to establish the TCP session.

    This wrapper builds a pyModbusTCP `ModbusClient`, opens the connection
    immediately (mirroring the source's eager `.connect()` call), and -- if
    it isn't already present -- attaches a `.connect()` alias pointing at
    `.open()` so the rest of this module (and any external callers) can keep
    calling `.connect()` without knowing the underlying client changed.
    """
    from pyModbusTCP.client import ModbusClient

    client = ModbusClient(host=host, port=port, auto_open=True)
    client.open()
    if not hasattr(client, "connect"):
        client.connect = client.open
    return client


def _axiom_read_holding_registers_wrapper(client, unit, address):
    """Bridge pymodbus's read_holding_registers response model to pyModbusTCP.

    pymodbus's `read_holding_registers(address, count, unit=unit)` returns a
    response object exposing `.isError()` and `.registers` (a list of the
    read values). pyModbusTCP's `ModbusClient.read_holding_registers(address,
    count)` instead returns a plain Python list on success or `None` on
    failure, has no response object, and takes the unit/slave id as the
    `unit_id` attribute on the client rather than a per-call keyword.

    This wrapper sets `client.unit_id` to emulate the dropped `unit=` kwarg,
    invokes the two-argument pyModbusTCP call, and wraps the list/None result
    in a tiny shim object exposing `.isError()` (True when the result is
    `None`) and `.registers` (the list, or `[]` on failure) so call sites
    written against the pymodbus response shape don't need to change.
    """
    client.unit_id = unit
    result = client.read_holding_registers(address, 1)

    class _ReadResult:
        def __init__(self, regs):
            self._regs = regs
            self.registers = regs if regs is not None else []

        def isError(self):
            return self._regs is None

    return _ReadResult(result)


def _axiom_write_register_wrapper(client, unit, address, value):
    """Bridge pymodbus's write_register response model to pyModbusTCP.

    pymodbus's `write_register(address, value, unit=unit)` returns a response
    object exposing `.isError()`. pyModbusTCP's
    `ModbusClient.write_single_register(address, value)` instead returns a
    plain bool (`True` on success, `False` on failure), has no response
    object, and takes the unit/slave id as the `unit_id` attribute on the
    client rather than a per-call keyword.

    This wrapper sets `client.unit_id` to emulate the dropped `unit=` kwarg,
    invokes the two-argument pyModbusTCP call, and wraps the boolean result
    in a tiny shim object exposing `.isError()` (True when the result is
    `False`) so call sites written against the pymodbus response shape don't
    need to change.
    """
    client.unit_id = unit
    ok = client.write_single_register(address, value)

    class _WriteResult:
        def __init__(self, ok):
            self._ok = ok

        def isError(self):
            return not self._ok

    return _WriteResult(ok)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_factory(host, port=port)
    client.connect()
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = _axiom_read_holding_registers_wrapper(self.client, self.unit, address)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = _axiom_write_register_wrapper(self.client, self.unit, address, counts)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
