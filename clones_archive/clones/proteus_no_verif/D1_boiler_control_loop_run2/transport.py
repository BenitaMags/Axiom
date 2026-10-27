"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _pymbtcp_client_factory(host: str, port: int = 502, unit: int = 1):
    """Construct a pyModbusTCP client that mimics pymodbus's ModbusTcpClient.

    pymodbus's ModbusTcpClient(host, port=port) returns a client object
    that is connected via .connect() and later used with a unit= kwarg on
    each read/write call. pyModbusTCP.client.ModbusClient instead takes
    host, port, unit_id, and auto_open in its constructor, opens the
    socket via .open() (which returns a bool rather than raising), and has
    no per-call unit kwarg -- the unit id is a persistent attribute on the
    client object instead. This wrapper builds the ModbusClient with the
    translated kwargs, stores the requested unit id on it (so downstream
    code can keep referencing self.unit / client.unit_id), and opens the
    connection, raising IOError if open() reports failure so callers see
    the same failure mode they would from a failed pymodbus connect().
    """
    from pyModbusTCP.client import ModbusClient

    client = ModbusClient(host=host, port=port, unit_id=unit, auto_open=True)
    if not client.open():
        raise IOError(f"Modbus TCP connect failed to {host}:{port}")
    return client


def _axiom_read_holding_registers_wrapper(client, address: int, unit: int):
    """Read one holding register while presenting a pymodbus-like response.

    pymodbus's read_holding_registers(address, count, unit=...) returns a
    response object exposing .isError() and .registers (the list of
    values read). pyModbusTCP's read_holding_registers(address, count)
    instead returns a plain list of register values on success or None on
    failure, and has no unit kwarg -- the unit id must be set on the
    client via client.unit_id beforehand. This wrapper sets client.unit_id,
    performs the read, and wraps the list/None result in a small object
    exposing .isError() (True when the underlying call returned None) and
    .registers (the returned list, or an empty list on error) so existing
    call sites written against the pymodbus response API keep working
    unmodified.
    """

    class _Result:
        def __init__(self, registers):
            self.registers = registers if registers is not None else []
            self._error = registers is None

        def isError(self):
            return self._error

    client.unit_id = unit
    registers = client.read_holding_registers(address, 1)
    return _Result(registers)


def _axiom_write_register_wrapper(client, address: int, counts: int, unit: int):
    """Write one holding register while presenting a pymodbus-like response.

    pymodbus's write_register(address, value, unit=...) returns a response
    object exposing .isError(). pyModbusTCP's write_single_register(
    address, value) instead returns a plain bool (True on success, False
    on failure) and has no unit kwarg -- the unit id must be set on the
    client via client.unit_id beforehand. This wrapper sets client.unit_id,
    performs the write, and wraps the bool result in a small object
    exposing .isError() (True when the underlying call returned False) so
    existing call sites written against the pymodbus response API keep
    working unmodified.
    """

    class _Result:
        def __init__(self, ok):
            self._error = not ok

        def isError(self):
            return self._error

    client.unit_id = unit
    ok = client.write_single_register(address, counts)
    return _Result(ok)


def _default_client(host: str, port: int = 502):

    return _pymbtcp_client_factory(host, port=port, unit=1)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = _axiom_read_holding_registers_wrapper(self.client, address, self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = _axiom_write_register_wrapper(self.client, address, counts, self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
