"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


class _ModbusTkResult:
    """Small stand-in for pymodbus's response object.

    pymodbus's ``read_holding_registers``/``write_register`` return an object
    exposing ``.registers`` (a list of ints, for reads) and ``.isError()``
    (a bool). modbus-tk's ``TcpMaster.execute`` instead returns a plain tuple
    of register values on success and *raises* an exception (e.g.
    ``ModbusError``/``ModbusInvalidResponseError``) on failure. This class
    re-wraps modbus-tk's result/exception into the same ``.registers`` /
    ``.isError()`` shape the call sites already expect, so ModbusIO does not
    need to change.
    """

    def __init__(self, registers=None, error=False):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


def _modbustk_client_compat_wrapper(host: str, port: int = 502):
    """Build a modbus-tk backed facade that mimics pymodbus's ModbusTcpClient.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()``,
    ``read_holding_registers(address, count, unit=...)`` and
    ``write_register(address, value, unit=...)``, where the latter two return
    a response object with ``.registers``/``.isError()``. modbus-tk instead
    exposes a ``TcpMaster`` with ``open()`` and a single ``execute(slave_id,
    function_code, address, ...)`` call that returns a plain tuple (or raises
    on failure), and it names the slave/unit id positionally as ``slave_id``
    rather than as a ``unit=`` keyword. This wrapper translates between the
    two: it maps ``connect()`` -> ``open()``, maps the pymodbus ``unit=``
    kwarg to modbus-tk's positional ``slave_id`` argument, and converts
    modbus-tk's tuple-or-exception result into a ``_ModbusTkResult`` exposing
    ``.registers``/``.isError()`` like pymodbus did. Only modbus-tk's own
    protocol/response exceptions (``ModbusError``, including its
    ``ModbusInvalidResponseError`` subclass) are translated into an error
    result, matching the failure modes pymodbus reported via ``isError()``;
    anything else (e.g. a bad host) still propagates.
    """
    import modbus_tk.defines as cst
    import modbus_tk.modbus_tcp as modbus_tcp
    from modbus_tk.exceptions import ModbusError, ModbusInvalidResponseError

    class _ModbusTkClientFacade:
        def __init__(self, host, port):
            self._master = modbus_tcp.TcpMaster(host, port)

        def connect(self):
            self._master.open()

        def read_holding_registers(self, address, count, unit=1):
            try:
                result = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except (ModbusError, ModbusInvalidResponseError):
                return _ModbusTkResult(registers=[], error=True)
            return _ModbusTkResult(registers=list(result), error=False)

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
            except (ModbusError, ModbusInvalidResponseError):
                return _ModbusTkResult(registers=[], error=True)
            return _ModbusTkResult(registers=[], error=False)

    return _ModbusTkClientFacade(host, port)


def _default_client(host: str, port: int = 502):

    client = _modbustk_client_compat_wrapper(host, port)
    client.connect()
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
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
