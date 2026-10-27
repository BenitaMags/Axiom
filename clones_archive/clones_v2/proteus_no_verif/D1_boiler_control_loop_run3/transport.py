"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbustk_client_adapter(host: str, port: int = 502):
    """Build a modbus-tk backed adapter mimicking the pymodbus client API.

    pymodbus's ModbusTcpClient exposes read_holding_registers(address, count,
    unit=...) and write_register(address, value, unit=...), each of which
    returns a response object with an .isError() method and, for reads, a
    .registers list of the values read.

    modbus-tk instead exposes a modbus_tcp.TcpMaster object. Connecting is
    done via TcpMaster.open(), and both reads and writes are issued through a
    single execute() method that takes a slave id and a Modbus function code
    (from modbus_tk.defines) and either returns the raw tuple of register
    values (for a read) or raises an exception (e.g.
    modbus_tk.exceptions.ModbusError or a socket error) if the request
    fails -- there is no response object with .isError().

    This wrapper bridges that gap: it opens a TcpMaster and returns a small
    adapter object whose connect()/read_holding_registers()/write_register()
    methods call TcpMaster.execute() with the appropriate function code,
    catch any exception raised by modbus-tk, and translate the outcome into
    a lightweight response object exposing .isError() and .registers -- the
    same shape _read1() and write_valve() already expect -- so their
    existing isError()-based error handling keeps working unchanged.
    """
    import modbus_tk.defines as cst
    from modbus_tk.modbus_tcp import TcpMaster

    class _Response:
        def __init__(self, registers=None, error=False):
            self.registers = registers
            self._error = error

        def isError(self):
            return self._error

    class _Adapter:
        def __init__(self, host, port):
            self._master = TcpMaster(host=host, port=port)
            self._master.set_timeout(5.0)

        def connect(self):
            try:
                self._master.open()
                return True
            except Exception:
                return False

        def read_holding_registers(self, address, count, unit=None):
            slave = unit if unit is not None else 1
            try:
                result = self._master.execute(
                    slave, cst.READ_HOLDING_REGISTERS, address, count
                )
                return _Response(registers=list(result), error=False)
            except Exception:
                return _Response(registers=None, error=True)

        def write_register(self, address, value, unit=None):
            slave = unit if unit is not None else 1
            try:
                self._master.execute(
                    slave,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
                return _Response(registers=None, error=False)
            except Exception:
                return _Response(registers=None, error=True)

    return _Adapter(host, port)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbustk_client_adapter(host, port=port)
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
