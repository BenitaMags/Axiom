"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's persistent-client API to modbus-tk's TcpMaster+execute pattern.

    pymodbus's ``ModbusTcpClient`` is a long-lived object exposing
    ``read_holding_registers(address, count, unit=...)`` and
    ``write_register(address, value, unit=...)`` methods that each return a
    response object with ``.isError()`` and ``.registers``. modbus-tk has no
    equivalent persistent client: instead it exposes a
    ``modbus_tk.modbus_tcp.TcpMaster`` whose single ``execute(slave,
    function_code, address, count=..., output_value=...)`` method returns a
    plain tuple of register values on success and raises a modbus_tk
    exception on failure -- there is no ``.isError()``/``.registers`` result
    object at all.

    This wrapper builds a small adapter that wraps a modbus_tk ``TcpMaster``
    and re-exposes ``.connect()``, ``.read_holding_registers(...)`` and
    ``.write_register(...)`` with the exact same call signatures and the
    same ``.isError()`` / ``.registers`` result shape that the rest of this
    module (``ModbusIO``) already depends on, so no other code has to
    change.
    """
    import modbus_tk.defines as cst
    import modbus_tk.modbus_tcp as modbus_tcp

    class _Result:
        def __init__(self, registers=None, error=False):
            if not (registers is not None):
                self._registers = []
            else:
                self._registers = list(registers)
            self._error = error

        def isError(self):
            _spt_r1 = self._error
            return _spt_r1

        @property
        def registers(self):
            _spt_r2 = self._registers
            return _spt_r2

    class _TcpMasterAdapter:
        def __init__(self, host, port):
            self._master = modbus_tcp.TcpMaster(host=host, port=port)
            self._master.set_timeout(5.0)

        def connect(self):
            self._master.open()

        def read_holding_registers(self, address, count, unit=1):
            try:
                values = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except Exception:
                _spt_r3 = _Result(error=True)
                return _spt_r3
            _spt_r4 = _Result(registers=values, error=False)
            return _spt_r4

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
            except Exception:
                _spt_r5 = _Result(error=True)
                return _spt_r5
            _spt_r6 = _Result(registers=[value], error=False)
            return _spt_r6
    _spt_r7 = _TcpMasterAdapter(host, port)

    return _spt_r7


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_tcp_client_wrapper(host, port)
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
        _spt_r8 = rr.registers[0]
        return _spt_r8

    def read_temperature(self, address: int = 0x0000) -> float:
        _spt_r9 = self._read1(address) / 10.0
        return _spt_r9  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        _spt_r10 = self._read1(address) / 100.0
        return _spt_r10  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
