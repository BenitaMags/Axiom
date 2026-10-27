"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Build a modbus-tk backed facade with the pymodbus client API this
    module was written against.

    pymodbus's ``ModbusTcpClient`` exposes named methods
    ``read_holding_registers(address, count, unit=...)`` and
    ``write_register(address, value, unit=...)`` that return response
    objects with an ``.isError()`` method and, on success, a ``.registers``
    list.

    modbus-tk instead exposes a single ``TcpMaster.execute(slave,
    function_code, address, quantity_of_x=..., output_value=...)`` call that
    uses numeric function-code constants (``modbus_tk.defines
    .READ_HOLDING_REGISTERS`` / ``.WRITE_SINGLE_REGISTER``), returns a plain
    tuple of register values on success, and raises a modbus_tk exception on
    failure instead of returning an "error" response object.

    This wrapper constructs a modbus_tk ``TcpMaster`` and returns a small
    facade object that re-implements ``read_holding_registers``/
    ``write_register`` on top of ``execute()``, catching any modbus_tk
    exception and translating both the success and failure cases into a
    lightweight response object exposing ``.registers`` and ``.isError()``,
    so the existing ``ModbusIO._read1``/``write_valve`` code keeps working
    unchanged.
    """
    import modbus_tk.defines as cst
    from modbus_tk import modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.set_timeout(5.0)

    class _Response:
        def __init__(self, registers=None, error=False):
            if not (registers is not None):
                self.registers = []
            else:
                self.registers = registers
            self._error = error

        def isError(self):
            _spt_r1 = self._error
            return _spt_r1

    class _ClientFacade:
        def __init__(self, master):
            self._master = master

        def connect(self):
            # modbus_tk opens its socket lazily on the first execute() call;
            # this method only exists for API parity with the old client.
            return True

        def read_holding_registers(self, address, count, unit=1):
            try:
                _spt_r2 = _Response(registers=list(self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )))
                return _spt_r2
            except Exception:
                _spt_r3 = _Response(error=True)
                return _spt_r3

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
                _spt_r4 = _Response(registers=[value])
                return _spt_r4
            except Exception:
                _spt_r5 = _Response(error=True)
                return _spt_r5
    _spt_r6 = _ClientFacade(master)

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
