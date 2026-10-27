"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Build a pymodbus-shaped client facade backed by modbus-tk.

    pymodbus's ``ModbusTcpClient`` exposes ``read_holding_registers(...)``
    and ``write_register(...)`` methods that return a response object with
    an ``.isError()`` method and a ``.registers`` list. modbus-tk has a very
    different API: a ``modbus_tk.modbus_tcp.TcpMaster`` is opened with
    ``.open()`` and reads/writes are performed through a single
    ``.execute(unit, function_code, address, ...)`` call that returns a
    plain tuple of register values on success and raises a
    ``modbus_tk.exceptions.ModbusError`` on failure (there is no
    ``.isError()``/``.registers`` shaped result at all).

    This wrapper creates a small facade class around a ``TcpMaster`` that
    re-exposes ``connect()``, ``read_holding_registers()`` and
    ``write_register()`` with the same call signatures and return shape
    (``.isError()`` / ``.registers``) that the rest of this module already
    expects, so ``_read1`` and ``write_valve`` do not need to change their
    error-handling logic.
    """
    import modbus_tk.defines as cst
    import modbus_tk.modbus_tcp as modbus_tcp
    from modbus_tk.exceptions import ModbusError

    class _Response:
        def __init__(self, registers=None, error=False):
            if not (registers is not None):
                self.registers = []
            else:
                self.registers = list(registers)
            self._error = error

        def isError(self):
            _spt_r1 = self._error
            return _spt_r1

    class _ClientFacade:
        def __init__(self, master):
            self._master = master

        def connect(self):
            self._master.open()

        def read_holding_registers(self, address, count, unit=1):
            try:
                _spt_r2 = _Response(registers=self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                ), error=False)
                return _spt_r2
            except ModbusError:
                _spt_r3 = _Response(registers=[], error=True)
                return _spt_r3

        def write_register(self, address, value, unit=1):
            try:
                _spt_r4 = _Response(registers=self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                ), error=False)
                return _spt_r4
            except ModbusError:
                _spt_r5 = _Response(registers=[], error=True)
                return _spt_r5
    _spt_r6 = _ClientFacade(modbus_tcp.TcpMaster(host=host, port=port))
    return _spt_r6


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_client_wrapper(host, port=port)
    client.connect()
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
        _spt_r7 = rr.registers[0]
        return _spt_r7

    def read_temperature(self, address: int = 0x0000) -> float:
        _spt_r8 = self._read1(address) / 10.0
        return _spt_r8  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        _spt_r9 = self._read1(address) / 100.0
        return _spt_r9  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
