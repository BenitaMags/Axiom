"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

def _axiom_modbus_client_factory_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's TCP client construction to minimalmodbus.

    pymodbus's ModbusTcpClient(host, port=port) opens a network (TCP/IP)
    connection to a remote Modbus server, and the caller must separately
    call .connect() before using it. minimalmodbus has no TCP transport at
    all: minimalmodbus.Instrument(port, slaveaddress) always wraps a local
    serial device (e.g. '/dev/ttyUSB0' or 'COM3') and opens the serial port
    immediately on construction -- there is no connect() step and no
    host/port network semantics to preserve.

    This wrapper cannot manufacture a real TCP-to-serial bridge (none
    exists in minimalmodbus), so it treats `host` as the serial device path
    minimalmodbus expects and builds an Instrument from it, using slave
    address 1 as a placeholder default. `port` is accepted only for
    call-site compatibility and is ignored, since minimalmodbus has no
    notion of a network port. Callers that still pass a real TCP host:port
    pair will fail at the minimalmodbus layer, which is expected -- this
    glue only restores a uniform call surface, it does not add TCP support.
    """
    import minimalmodbus

    instrument = minimalmodbus.Instrument(host, 1)
    return instrument


def _axiom_read_holding_registers_wrapper(client, address: int, unit: int) -> int:
    """Bridge pymodbus's read_holding_registers() to minimalmodbus's read_register().

    pymodbus's read_holding_registers(address, count, unit=unit) returns a
    response object exposing .isError() and .registers, and takes the slave
    id as a per-call `unit=` keyword argument. minimalmodbus's
    Instrument.read_register(address, ...) instead returns the raw integer
    value directly and raises an exception (e.g. minimalmodbus.ModbusException
    / serial errors) on failure, and it binds the slave address on the
    instrument itself (client.address) rather than accepting it per call.

    This wrapper sets client.address = unit immediately before reading (to
    emulate per-call unit selection), calls read_register(), and converts
    any raised exception into an IOError so existing callers that used to
    branch on rr.isError() keep working via ordinary exception handling.
    """
    try:
        client.address = unit
        return client.read_register(address, number_of_decimals=0, functioncode=3)
    except Exception as exc:
        raise IOError(f"Modbus read failed @ {address:#06x}") from exc


def _axiom_write_register_wrapper(client, address: int, counts: int, unit: int) -> None:
    """Bridge pymodbus's write_register() to minimalmodbus's write_register().

    pymodbus's write_register(address, value, unit=unit) returns a response
    object exposing .isError(), and takes the slave id as a per-call
    `unit=` keyword argument. minimalmodbus's Instrument.write_register(...)
    instead returns None on success and raises an exception on failure, and
    it binds the slave address on the instrument itself (client.address)
    rather than accepting it per call.

    This wrapper sets client.address = unit immediately before writing (to
    emulate per-call unit selection), performs the write, and converts any
    raised exception into an IOError so existing callers that used to check
    wr.isError() keep working via ordinary exception handling.
    """
    try:
        client.address = unit
        client.write_register(address, counts, number_of_decimals=0, functioncode=6)
    except Exception as exc:
        raise IOError(f"Modbus write failed @ {address:#06x}") from exc


def _default_client(host: str, port: int = 502):
    from pymodbus.client import ModbusTcpClient  # lazy import for testability

    client = _axiom_modbus_client_factory_wrapper(host, port=port)
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        return _axiom_read_holding_registers_wrapper(self.client, address, self.unit)

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        _axiom_write_register_wrapper(self.client, address, counts, self.unit)
        return counts
