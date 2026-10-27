"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

def _axiom_modbus_client_factory(host: str, port: int = 502, unit: int = 1):
    """Bridge pymodbus's ModbusTcpClient construction/connect API to pyModbusTCP.

    pymodbus's ``ModbusTcpClient(host, port=port)`` returns a client object
    that is later connected via ``.connect()``, and its per-call methods
    accept a ``unit=`` keyword for the slave/unit id.

    pyModbusTCP's ``ModbusClient`` instead takes ``host``, ``port`` and
    ``unit_id`` at construction time, and opens the TCP socket via
    ``.open()`` -- it has no ``.connect()`` method at all.

    This factory builds a pyModbusTCP ``ModbusClient`` with host/port/unit
    mapped from the old ``ModbusTcpClient(host, port=port)`` call, and
    monkey-patches a ``.connect()`` alias onto the instance (bound to
    ``.open()``) so that existing call sites which invoke ``client.connect()``
    keep working unchanged. The read/write incompatibilities themselves are
    handled separately by ``_axiom_read_holding_registers_wrapper`` and
    ``_axiom_write_register_wrapper``.
    """
    from pyModbusTCP.client import ModbusClient

    client = ModbusClient(host=host, port=port, unit_id=unit)
    client.connect = client.open
    return client


def _axiom_read_holding_registers_wrapper(client, address: int):
    """Bridge pymodbus's read_holding_registers() result to pyModbusTCP's.

    pymodbus's ``read_holding_registers(address, count, unit=...)`` returns
    a response object whose failure is checked with ``rr.isError()`` and
    whose value is read from ``rr.registers[0]``.

    pyModbusTCP's ``read_holding_registers(address, number)`` instead
    returns a plain list of register values on success, or ``None`` on
    failure -- there is no response object and no ``isError()``.

    This wrapper calls pyModbusTCP's ``read_holding_registers(address, 1)``
    (dropping the unused ``unit=`` kwarg, since pyModbusTCP's unit id lives
    on the client instance instead), raises ``IOError`` when the result is
    ``None`` to preserve the original exception-raising contract expected
    by ``ModbusIO._read1``, and returns the first element of the returned
    list in place of the old ``rr.registers[0]``.
    """
    result = client.read_holding_registers(address, 1)
    if result is None:
        raise IOError(f"Modbus read failed @ {address:#06x}")
    return result[0]


def _axiom_write_register_wrapper(client, address: int, value: int):
    """Bridge pymodbus's write_register() result to pyModbusTCP's.

    pymodbus's ``write_register(address, value, unit=...)`` returns a
    response object whose failure is checked with ``wr.isError()``.

    pyModbusTCP's ``write_single_register(address, value)`` instead
    returns ``True`` on success and ``False``/``None`` on failure, with no
    response object to inspect.

    This wrapper calls pyModbusTCP's ``write_single_register(address,
    counts)`` (dropping the unused ``unit=`` kwarg, handled by the
    client's unit id instead), and translates a falsy result into the
    ``IOError`` originally raised by ``ModbusIO.write_valve`` on failure.
    """
    ok = client.write_single_register(address, value)
    if not ok:
        raise IOError(f"Modbus write failed @ {address:#06x}")
    return True

def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_factory(host, port=port)
    client.connect()
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        return _axiom_read_holding_registers_wrapper(self.client, address)

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        _axiom_write_register_wrapper(self.client, address, counts)
        return counts
