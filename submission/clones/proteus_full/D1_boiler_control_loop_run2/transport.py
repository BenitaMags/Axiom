"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_wrapper(host: str, port: int = 502, unit: int = 1):
    """Bridge for constructing/connecting a Modbus TCP client.

    pymodbus builds a client via ModbusTcpClient(host, port=port) and then
    opens the socket with an explicit .connect() call; per-request calls
    later pass a 'unit' kwarg to address a particular slave.

    pyModbusTCP instead exposes pyModbusTCP.client.ModbusClient(host=...,
    port=...), opens its socket via .open() (not .connect()), and expects
    the target slave/unit id to be set once as a client.unit_id attribute
    rather than being passed on every call.

    This wrapper constructs the pyModbusTCP.client.ModbusClient, opens it,
    and stashes the desired unit id on client.unit_id. It then attaches
    pymodbus-style read_holding_registers/write_register methods to the
    client object (each accepting and discarding a 'unit' kwarg, since the
    unit id already lives on client.unit_id) so callers written against the
    pymodbus API can keep using the same method names/signatures.
    """
    from pyModbusTCP.client import ModbusClient

    client = ModbusClient(host=host, port=port)
    client.open()
    client.unit_id = unit

    def _read_holding_registers(address, count=1, unit=None):
        return _axiom_read_holding_registers_wrapper(client, address, client.unit_id)

    def _write_register(address, value, unit=None):
        return _axiom_write_register_wrapper(client, address, value, client.unit_id)

    client.read_holding_registers = _read_holding_registers
    client.write_register = _write_register
    return client


class _AxiomModbusReadResult:
    """Lightweight stand-in for pymodbus's read response object.

    pymodbus's read_holding_registers() returns a response object with an
    .isError() method and a .registers list. pyModbusTCP's
    read_holding_registers(address, count) instead returns a plain list of
    register values on success or None on failure, with no unified error
    object. This class re-adds the .isError()/.registers surface that
    ModbusIO's call sites already rely on.
    """

    def __init__(self, values):
        self._values = values

    def isError(self) -> bool:
        return self._values is None

    @property
    def registers(self):
        return self._values


class _AxiomModbusWriteResult:
    """Lightweight stand-in for pymodbus's write response object.

    pymodbus's write_register() returns a response object with an
    .isError() method. pyModbusTCP's write_single_register(address, value)
    instead returns True/False (or None) with no error object. This class
    re-adds the .isError() surface that ModbusIO's call sites already rely
    on.
    """

    def __init__(self, ok):
        self._ok = ok

    def isError(self) -> bool:
        return not bool(self._ok)


def _axiom_read_holding_registers_wrapper(client, address, unit):
    """Perform a holding-register read using pyModbusTCP's API.

    pymodbus's client.read_holding_registers(address, 1, unit=unit) reads
    one register for a given slave/unit id and returns a response object
    exposing .isError()/.registers. pyModbusTCP's client instead exposes
    read_holding_registers(address, count) with the slave/unit id set
    beforehand via client.unit_id, and returns a plain list (or None on
    failure). This wrapper sets client.unit_id, performs the pyModbusTCP
    read, and wraps the resulting list/None into an
    _AxiomModbusReadResult so existing rr.isError()/rr.registers call
    chains keep working unchanged.
    """
    client.unit_id = unit
    values = client.read_holding_registers(address, 1)
    return _AxiomModbusReadResult(values)


def _axiom_write_register_wrapper(client, address, counts, unit):
    """Perform a single holding-register write using pyModbusTCP's API.

    pymodbus's client.write_register(address, counts, unit=unit) writes one
    register for a given slave/unit id and returns a response object
    exposing .isError(). pyModbusTCP's client instead exposes
    write_single_register(address, value) with the slave/unit id set
    beforehand via client.unit_id, and returns True/False/None with no
    error object. This wrapper sets client.unit_id, performs the
    pyModbusTCP write, and wraps the resulting bool into an
    _AxiomModbusWriteResult so existing wr.isError() call chains keep
    working unchanged.
    """
    client.unit_id = unit
    ok = client.write_single_register(address, counts)
    return _AxiomModbusWriteResult(ok)


def _default_client(host: str, port: int = 502):

    return _axiom_modbus_client_wrapper(host, port=port)


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
