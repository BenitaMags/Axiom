"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


class _axiom_modbus_response(object):
    """Pseudo-response object mimicking pymodbus's response objects.

    pymodbus's read/write calls return a response object exposing an
    ``isError()`` method and, for reads, a ``.registers`` list of values.
    pyModbusTCP's calls instead return a raw list of register values (or
    ``None`` on failure) for reads, and a bare ``bool`` for writes. This tiny
    shim wraps whichever raw value pyModbusTCP returned so call sites can
    keep using ``.isError()`` / ``.registers`` unchanged.
    """

    def __init__(self, error, registers=None):
        self._error = error
        self.registers = registers

    def isError(self):
        return self._error


def _axiom_read_holding_registers_wrapper(client, address, count, unit):
    """Bridge pyModbusTCP's read_holding_registers to a pymodbus-like response.

    pymodbus's ``read_holding_registers(address, count, unit=...)`` returns a
    response object exposing ``.isError()`` and ``.registers``. pyModbusTCP's
    ``ModbusClient.read_holding_registers(address, count)`` has no ``unit``
    keyword -- the unit/slave id is instead set via the client's ``unit_id``
    attribute -- and it returns a plain list of register values, or ``None``
    on error, with no ``.isError()``/``.registers`` accessors. This wrapper
    sets ``client.unit_id`` before the call, invokes pyModbusTCP's
    ``read_holding_registers(address, count)``, and synthesizes a
    pseudo-response object (``.isError()`` True when the result is ``None``,
    ``.registers`` holding the returned list) so existing call sites keep
    working unchanged.
    """
    client.unit_id = unit
    result = client.read_holding_registers(address, count)
    return _axiom_modbus_response(result is None, result)


def _axiom_write_register_wrapper(client, address, value, unit):
    """Bridge pyModbusTCP's write_single_register to a pymodbus-like response.

    pymodbus's ``write_register(address, value, unit=...)`` returns a
    response object exposing ``.isError()``. pyModbusTCP's
    ``ModbusClient.write_single_register(address, value)`` has no ``unit``
    keyword -- the unit/slave id is instead set via the client's ``unit_id``
    attribute -- and it returns a plain bool indicating success/failure
    instead of a response object. This wrapper sets ``client.unit_id`` before
    the call, invokes pyModbusTCP's ``write_single_register(address, value)``,
    and synthesizes a pseudo-response object whose ``.isError()`` is True
    when the underlying call returned False, so existing call sites keep
    working unchanged.
    """
    client.unit_id = unit
    success = client.write_single_register(address, value)
    return _axiom_modbus_response(success is False)


class _axiom_modbus_tcp_client_wrapper(object):
    """Adapter presenting a pymodbus-like client surface backed by pyModbusTCP.

    pymodbus's ``ModbusTcpClient(host, port=port)`` is instantiated and then
    connected via a ``connect()`` method that returns a bool. pyModbusTCP's
    ``ModbusClient(host, port=port)`` instead connects via an ``open()``
    method. This compatibility class instantiates a pyModbusTCP
    ``ModbusClient`` internally and re-exposes a pymodbus-like surface
    (``connect()``, ``read_holding_registers()``, ``write_register()``) so
    that code constructing a client (``client = ...; client.connect()``)
    needs no further changes, while the underlying raw client remains
    reachable via ``.raw`` for the read/write wrappers used elsewhere.
    """

    def __init__(self, host, port=502):
        from pyModbusTCP.client import ModbusClient

        self.raw = ModbusClient(host, port=port)

    def connect(self):
        """Mirror pymodbus's ``connect()`` using pyModbusTCP's ``open()``."""
        return self.raw.open()

    def read_holding_registers(self, address, count, unit=1):
        """Mirror pymodbus's read call via the shared read wrapper."""
        return _axiom_read_holding_registers_wrapper(self.raw, address, count, unit)

    def write_register(self, address, value, unit=1):
        """Mirror pymodbus's write call via the shared write wrapper."""
        return _axiom_write_register_wrapper(self.raw, address, value, unit)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_tcp_client_wrapper(host, port=port)
    client.connect()
    return client.raw


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = _axiom_read_holding_registers_wrapper(self.client, address, 1, self.unit)
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
