"""modbus_adapter -- pyModbusTCP client construction and adapter.

Bridges pyModbusTCP's client (unit_id attribute, list/None and bool/None
return conventions) to the method names and IOError-on-failure contract that
callers written against pymodbus's ModbusTcpClient expect.
"""


def _axiom_read_holding_registers_wrapper(client, address, unit):
    """
    pymodbus's ModbusTcpClient.read_holding_registers(address, count, unit=unit)
    returns a response object whose failure is checked via rr.isError() and whose
    values live in rr.registers. pyModbusTCP's ModbusClient.read_holding_registers
    takes no unit= keyword -- the slave/unit id is set once on the client via the
    unit_id attribute -- and returns a plain list of register values on success or
    None on any failure (timeout, connection lost, exception response, malformed
    reply, ...). This wrapper sets unit_id, calls read_holding_registers(address, 1)
    on the raw pyModbusTCP client, treats a None reply as the same failure pymodbus
    reported through isError() (raising IOError, exactly as the original call site
    already did), and returns the single register value the same way rr.registers[0]
    did, so callers don't need to know which library answered the request.
    """
    client.unit_id = unit
    rr = client.read_holding_registers(address, 1)
    if rr is None:
        raise IOError(f"Modbus read failed @ {address:#06x}")
    return rr[0]


def _axiom_write_register_wrapper(client, address, counts, unit):
    """
    pymodbus's ModbusTcpClient.write_register(address, value, unit=unit) returns a
    response object whose failure is checked via wr.isError(). pyModbusTCP's
    ModbusClient.write_single_register(address, value) takes no unit= keyword --
    the slave/unit id is set once on the client via the unit_id attribute -- and
    returns True on success or False/None on failure instead of a response object.
    This wrapper sets unit_id, calls write_single_register on the raw pyModbusTCP
    client, and raises IOError on a falsy return the same way the original call
    site raised IOError from wr.isError(), then returns counts on success just as
    the original code did.
    """
    client.unit_id = unit
    wr = client.write_single_register(address, counts)
    if not wr:
        raise IOError(f"Modbus write failed @ {address:#06x}")
    return counts


class _AxiomModbusClientAdapter:
    """
    Adapts a pyModbusTCP ModbusClient so it can stand in for a pymodbus
    ModbusTcpClient. pymodbus's client exposes read_holding_registers/
    write_register methods that accept a unit= kwarg per call and return response
    objects with .isError()/.registers. pyModbusTCP's ModbusClient instead expects
    the unit id to be set once via .unit_id and its read/write methods return
    plain lists/None or True/False. This adapter holds the raw pyModbusTCP client
    and re-exposes the same method names (read_holding_registers, write_register)
    that pymodbus's client offered, delegating to the _axiom_*_wrapper functions
    above to translate pyModbusTCP's None/False failure returns into the IOError
    that ModbusIO already expects on a failed read/write, so no pyModbusTCP
    exception or return convention leaks past this adapter.
    """

    def __init__(self, client):
        self._client = client

    def connect(self):
        return self._client.open()

    def close(self):
        return self._client.close()

    def read_holding_registers(self, address, unit):
        return _axiom_read_holding_registers_wrapper(self._client, address, unit)

    def write_register(self, address, counts, unit):
        return _axiom_write_register_wrapper(self._client, address, counts, unit)


def _axiom_modbus_client_factory(host: str, port: int = 502):
    """
    pymodbus's ModbusTcpClient(host, port=port) constructs a client and its
    connect() call opens the socket, returning a bool. pyModbusTCP's
    ModbusClient(host=host, port=port) is constructed similarly but opens the
    connection through .open() instead of .connect(), and its read/write methods
    use a different return convention (plain lists/None/bool instead of response
    objects with .isError()/.registers) plus a unit_id attribute instead of a
    unit= kwarg per call. This factory builds and opens a pyModbusTCP
    ModbusClient, then wraps it in _AxiomModbusClientAdapter so callers keep using
    the same read_holding_registers/write_register method names and IOError
    failure contract that pymodbus's client offered, instead of receiving the raw
    pyModbusTCP object (which would leak pyModbusTCP's different return/exception
    conventions to callers written against pymodbus).
    """
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port)
    client.open()
    return _AxiomModbusClientAdapter(client)
