"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _modbus_tk_client_wrapper(host: str, port: int = 502):
    """Build a modbus-tk backed client that mimics the pymodbus ModbusTcpClient API.

    pymodbus's ModbusTcpClient exposes read_holding_registers(address, count, unit=...)
    and write_register(address, value, unit=...), each returning a response object with
    an .isError() method and (for reads) a .registers list. modbus-tk's TcpMaster instead
    exposes a single execute(slave, function_code, address, ...) call that returns a raw
    tuple/list of register values (or raises an exception on failure) and has no notion
    of a unit= keyword or of response objects with .isError()/.registers.

    This wrapper builds a small shim class around modbus_tk.modbus_tcp.TcpMaster that
    translates the old-style calls (read_holding_registers/write_register with a unit=
    kwarg) into master.execute(unit, cst.READ_HOLDING_REGISTERS/WRITE_SINGLE_REGISTER,
    address, ...) calls, catching any exception (or empty result) and wrapping the
    outcome into a tiny response object exposing .isError() and .registers -- so the
    rest of this module (rr.isError(), rr.registers[0], wr.isError()) keeps working
    unchanged.
    """
    import modbus_tk.defines as cst
    from modbus_tk.modbus_tcp import TcpMaster

    class _Response:
        def __init__(self, registers=None, error=False):
            if not (registers is not None):
                self.registers = []
            else:
                self.registers = list(registers)
            self._error = error

        def isError(self) -> bool:
            _spt_r1 = self._error
            return _spt_r1

    class _ModbusTkClient:
        def __init__(self, host: str, port: int):
            self._master = TcpMaster(host=host, port=port)
            self._master.set_timeout(5.0)

        def connect(self) -> bool:
            # modbus-tk's TcpMaster opens its socket lazily on first execute(),
            # so there is nothing to eagerly connect here; kept for API parity.
            return True

        def close(self) -> None:
            self._master.close()

        def read_holding_registers(self, address: int, count: int = 1, unit: int = 1):
            try:
                result = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except Exception:
                _spt_r2 = _Response(error=True)
                return _spt_r2
            if not result:
                _spt_r3 = _Response(error=True)
                return _spt_r3
            _spt_r4 = _Response(registers=result)
            return _spt_r4

        def write_register(self, address: int, value: int, unit: int = 1):
            try:
                result = self._master.execute(
                    unit, cst.WRITE_SINGLE_REGISTER, address, output_value=value
                )
            except Exception:
                _spt_r5 = _Response(error=True)
                return _spt_r5
            if not result:
                _spt_r6 = _Response(error=True)
                return _spt_r6
            _spt_r7 = _Response(registers=result)
            return _spt_r7
    _spt_r8 = _ModbusTkClient(host, port)

    return _spt_r8


def _default_client(host: str, port: int = 502):

    client = _modbus_tk_client_wrapper(host, port)
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
        _spt_r9 = rr.registers[0]
        return _spt_r9

    def read_temperature(self, address: int = 0x0000) -> float:
        _spt_r10 = self._read1(address) / 10.0
        return _spt_r10  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        _spt_r11 = self._read1(address) / 100.0
        return _spt_r11  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
