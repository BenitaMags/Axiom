"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""
from modbus_shim import build_pymodbus_style_client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else build_pymodbus_style_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts

### FILENAME: modbus_shim.py
"""modbus_shim -- translation layer between the pymodbus-shaped client API
used by transport.ModbusIO and the modbus_tk library actually installed.

This module holds the substitution glue described in transport.py: the
pymodbus client API (``connect``/``read_holding_registers``/
``write_register`` returning ``isError()``-bearing responses) is emulated
on top of modbus_tk's ``TcpMaster`` (``open``/``close``/``execute`` that
raises on failure).
"""


class ModbusResponse:
    """Lightweight stand-in for pymodbus's response objects.

    pymodbus's read/write calls return a response object with an
    ``isError()`` method and, for reads, a ``.registers`` list of the
    values read. modbus-tk's ``TcpMaster.execute`` instead either returns
    a tuple of register values directly (on success) or raises an
    exception (on failure). This class re-packages either outcome into
    the pymodbus-shaped object so existing call sites that check
    ``.isError()`` and read ``.registers`` keep working unchanged.
    """

    def __init__(self, registers=None, exception=None):
        self.registers = list(registers) if registers is not None else []
        self._exception = exception

    def isError(self) -> bool:
        return self._exception is not None


def _execute_guarded(master, unit, function_code, address, **kwargs):
    """Run master.execute(...), turning modbus_tk failures into a response.

    Returns a tuple ``(result, response_or_none)``: on success, ``result``
    is whatever ``execute`` returned and ``response_or_none`` is ``None``;
    on failure, ``result`` is ``None`` and ``response_or_none`` is an
    error-carrying ``ModbusResponse``.
    """
    import modbus_tk.exceptions as mtk_exceptions

    try:
        result = master.execute(unit, function_code, address, **kwargs)
    except (mtk_exceptions.ModbusError, mtk_exceptions.ModbusInvalidResponseError) as exc:
        return None, ModbusResponse(exception=exc)
    except OSError as exc:
        return None, ModbusResponse(exception=exc)
    return result, None


class PymodbusStyleClient:
    """Wraps a modbus_tk TcpMaster to expose the pymodbus client API used here.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()``,
    ``read_holding_registers(address, count, unit=...)`` and
    ``write_register(address, value, unit=...)``, all returning response
    objects with ``isError()``. modbus-tk's ``TcpMaster`` instead exposes
    ``open()``/``close()`` and a single ``execute(slave, function_code,
    address, ...)`` method that raises a modbus_tk exception on failure
    rather than returning an error flag. This shim translates between the
    two: ``connect`` calls ``open``, and the read/write methods call
    ``execute`` with the appropriate function code, catching modbus_tk's
    exceptions and reporting them via ``ModbusResponse`` instead of
    letting them propagate with a foreign type, mirroring the
    isError()-based contract the rest of this file relies on.
    """

    def __init__(self, master):
        self._master = master

    def connect(self):
        self._master.open()

    def read_holding_registers(self, address, count, unit=1):
        import modbus_tk.defines as cst

        result, error_response = _execute_guarded(
            self._master, unit, cst.READ_HOLDING_REGISTERS, address, count
        )
        if error_response is not None:
            return error_response
        return ModbusResponse(registers=result)

    def write_register(self, address, value, unit=1):
        import modbus_tk.defines as cst

        _, error_response = _execute_guarded(
            self._master, unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
        )
        if error_response is not None:
            return error_response
        return ModbusResponse()


def build_pymodbus_style_client(host: str, port: int = 502):
    """Build a modbus-tk TCP master, open it, and return a pymodbus-shaped client.

    pymodbus's ``ModbusTcpClient(host, port=port)`` returns an object that
    is later ``connect()``-ed and then used for
    ``read_holding_registers``/``write_register`` calls returning
    ``isError()``-bearing responses. modbus-tk instead requires
    constructing a ``modbus_tk.modbus_tcp.TcpMaster(host, port=port)`` and
    calling ``execute(...)`` with explicit function codes, raising on
    failure. This function builds and opens the TcpMaster and wraps it in
    ``PymodbusStyleClient`` so callers keep using the pymodbus-style API.
    """
    import modbus_tk.modbus_tcp as modbus_tcp  # lazy import for testability

    master = modbus_tcp.TcpMaster(host=host, port=port)
    client = PymodbusStyleClient(master)
    client.connect()
    return client
