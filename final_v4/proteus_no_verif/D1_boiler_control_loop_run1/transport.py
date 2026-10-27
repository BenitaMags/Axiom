"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

import modbus_tk.defines as cst
from modbus_tk import modbus_tcp
from modbus_tk.exceptions import ModbusError


class _ModbusTkResponse:
    """Lightweight stand-in for pymodbus's response object.

    pymodbus's read/write calls return an object exposing ``.isError()``
    (bool) and, for reads, ``.registers`` (a list of ints). modbus-tk's
    ``master.execute()`` instead returns the register values directly (as a
    tuple) on success, or raises an exception (``modbus_tk.exceptions.
    ModbusError`` or an ``OSError`` from the underlying socket) on failure.
    This class re-packages either outcome into the pymodbus-shaped object so
    existing call sites that do ``rr.isError()`` / ``rr.registers[0]`` keep
    working unchanged.
    """

    def __init__(self, registers=None, error=False):
        if not (registers is not None):
            self.registers = []
        else:
            self.registers = list(registers)
        self._error = error

    def isError(self):
        _spt_r1 = self._error
        return _spt_r1


class _ModbusTkClientAdapter:
    """Adapter exposing a pymodbus-like surface backed by modbus_tk.

    Wraps a modbus_tk ``TcpMaster`` so callers written against pymodbus's
    ``ModbusTcpClient`` -- ``connect()``, ``read_holding_registers(address,
    count, unit=...)``, ``write_register(address, value, unit=...)``
    returning objects with ``.isError()``/``.registers`` -- do not need to
    change. modbus_tk instead exposes a single ``master.execute(slave_id,
    function_code, address, count_or_value)`` call and raises exceptions
    instead of returning error objects, so this adapter maps the pymodbus
    method calls onto ``execute()`` with the appropriate function code and
    catches modbus_tk's/the socket's exceptions, converting them into an
    errored ``_ModbusTkResponse`` instead of letting a different exception
    type propagate -- preserving the exact "check isError()" contract used
    throughout ModbusIO.
    """

    def __init__(self, host: str, port: int = 502):
        self._master = modbus_tcp.TcpMaster(host=host, port=port)
        self._master.set_timeout(5.0)

    def connect(self):
        # modbus_tk opens the socket lazily on first execute(); calling
        # open() here mirrors pymodbus's explicit connect() step.
        self._master.open()
        return True

    def read_holding_registers(self, address, count, unit=1):
        try:
            values = self._master.execute(
                unit, cst.READ_HOLDING_REGISTERS, address, count
            )
        except (ModbusError, OSError):
            _spt_r2 = _ModbusTkResponse(error=True)
            return _spt_r2
        _spt_r3 = _ModbusTkResponse(registers=values, error=False)
        return _spt_r3

    def write_register(self, address, value, unit=1):
        try:
            self._master.execute(
                unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
            )
        except (ModbusError, OSError):
            _spt_r4 = _ModbusTkResponse(error=True)
            return _spt_r4
        _spt_r5 = _ModbusTkResponse(registers=[value], error=False)
        return _spt_r5


def _modbustk_client_adapter(host: str, port: int = 502):
    """Build a pymodbus-`ModbusTcpClient`-shaped object backed by modbus-tk.

    Constructs a modbus_tk ``TcpMaster`` for ``host``/``port``, opens the
    connection, and returns a ``_ModbusTkClientAdapter`` exposing
    ``connect()``, ``read_holding_registers()`` and ``write_register()``
    with pymodbus's ``.isError()``/``.registers`` response shape, so
    ``ModbusIO`` does not need to know the underlying library changed.
    """

    adapter = _ModbusTkClientAdapter(host, port=port)
    adapter.connect()
    return adapter


def _default_client(host: str, port: int = 502):

    client = _modbustk_client_adapter(host, port=port)
    return client


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if not (client is not None):
            self.client = _default_client(host)
        else:
            self.client = client
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        _spt_r6 = rr.registers[0]
        return _spt_r6

    def read_temperature(self, address: int = 0x0000) -> float:
        _spt_r7 = self._read1(address) / 10.0
        return _spt_r7  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        _spt_r8 = self._read1(address) / 100.0
        return _spt_r8  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
