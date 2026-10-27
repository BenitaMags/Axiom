"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_factory(host: str, port: int, unit: int):
    """Bridge pymodbus's ModbusTcpClient construction/connect API to pyModbusTCP.

    pymodbus's ModbusTcpClient is constructed as ``ModbusTcpClient(host,
    port=port)`` and exposes a ``.connect()`` method that opens the TCP
    socket, plus ``.read_holding_registers()``/``.write_register()`` methods
    that each accept a ``unit=`` keyword per call. pyModbusTCP's
    ModbusClient instead takes the unit id once, at construction time (as
    ``unit_id=``), opens the socket via ``.open()`` rather than
    ``.connect()``, and its register methods (``read_holding_registers``/
    ``write_single_register``) take no per-call unit argument and return
    plain lists/booleans (or ``None``/``False`` on failure) instead of
    pymodbus-style response objects with ``.isError()``/``.registers``.

    This factory constructs a pyModbusTCP ``ModbusClient`` bound to the
    given host/port/unit, and attaches a ``connect`` method that is just an
    alias for ``open()`` so existing call sites written against the
    pymodbus API (``client.connect()``) keep working unchanged.
    """
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, unit_id=unit)
    client.connect = client.open
    return client


def _axiom_read_holding_registers_wrapper(client, address: int, unit: int):
    """Bridge pymodbus's read_holding_registers() response to pyModbusTCP's.

    pymodbus's ``client.read_holding_registers(address, count, unit=unit)``
    returns a response object exposing ``.isError()`` and ``.registers``.
    pyModbusTCP's ``client.read_holding_registers(address, count)`` (no
    per-call ``unit=``, since the unit id is fixed on the client at
    construction time) instead returns a plain list of register values on
    success, or ``None`` on failure.

    This wrapper calls the pyModbusTCP method and wraps its result in a
    small shim object exposing ``.isError()`` (True when the underlying call
    returned ``None``) and ``.registers`` (the returned list, or an empty
    list on failure), so existing call sites written against the pymodbus
    response API keep working unchanged.
    """

    class _Result:
        def __init__(self, regs):
            self._regs = regs

        def isError(self):
            return self._regs is None

        @property
        def registers(self):
            return self._regs if self._regs is not None else []

    regs = client.read_holding_registers(address, 1)
    return _Result(regs)


def _axiom_write_register_wrapper(client, address: int, value: int, unit: int):
    """Bridge pymodbus's write_register() response to pyModbusTCP's.

    pymodbus's ``client.write_register(address, value, unit=unit)`` returns
    a response object exposing ``.isError()``. pyModbusTCP's
    ``client.write_single_register(address, value)`` (no per-call ``unit=``)
    instead returns a plain boolean: ``True`` on success, ``False``/``None``
    on failure.

    This wrapper calls the pyModbusTCP method and wraps the boolean result
    in a small shim object exposing ``.isError()`` (True when the underlying
    call did not return ``True``), so the existing
    ``if wr.isError(): raise IOError(...)`` pattern keeps working unchanged.
    """

    class _Result:
        def __init__(self, ok):
            self._ok = ok

        def isError(self):
            return not self._ok

    ok = client.write_single_register(address, value)
    return _Result(ok)


def _default_client(host: str, port: int = 502, unit: int = 1):

    client = _axiom_modbus_client_factory(host, port, unit)
    client.connect()
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.unit = unit
        self.client = client if client is not None else _default_client(host, unit=unit)

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
