"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""
def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient API onto modbus-tk's TcpMaster.

    pymodbus's ModbusTcpClient exposes connect()/close() plus
    read_holding_registers(address, count, unit=...) and
    write_register(address, value, unit=...), each returning a response
    object with .isError() and (for reads) a .registers list.

    modbus-tk's TcpMaster instead uses open()/close() for the connection
    lifecycle and a single execute(slave, function_code, address,
    count_or_value) call that either returns a tuple of register values
    (for reads) or raises modbus_tk.exceptions.ModbusError on any failure
    (there is no return-value error signalling like pymodbus's isError()).

    This wrapper builds a modbus_tk.modbus_tcp.TcpMaster, opens it, and
    returns a small facade object that mimics the pymodbus surface
    ModbusIO relies on: connect()/close(), plus read_holding_registers()
    and write_register() that call TcpMaster.execute() under the hood and
    translate a raised ModbusError into a pymodbus-style response object
    with .isError() == True (and empty .registers), or wrap a successful
    read's values into a response with .isError() == False and
    .registers set. This lets the rest of ModbusIO's call chain
    (rr.isError(), rr.registers[0], wr.isError()) keep working unmodified.
    """
    import modbus_tk.defines as cst
    from modbus_tk.exceptions import ModbusError
    from modbus_tk.modbus_tcp import TcpMaster

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
            self._master.open()

        def close(self):
            self._master.close()

        def read_holding_registers(self, address, count, unit=1):
            try:
                values = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except ModbusError:
                _spt_r2 = _Response(error=True)
                return _spt_r2
            _spt_r3 = _Response(registers=list(values))
            return _spt_r3

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit, cst.WRITE_SINGLE_REGISTER, address, value
                )
            except ModbusError:
                _spt_r4 = _Response(error=True)
                return _spt_r4
            _spt_r5 = _Response()
            return _spt_r5

    master = TcpMaster(host, port)
    facade = _ClientFacade(master)
    facade.connect()
    return facade


def _default_client(host: str, port: int = 502):
    _spt_r6 = _axiom_modbus_tcp_client_wrapper(host, port=port)

    return _spt_r6


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
