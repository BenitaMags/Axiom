"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_tcp_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient API onto modbus-tk's TcpMaster.

    pymodbus's ModbusTcpClient exposes .connect(), and its
    read_holding_registers()/write_register() calls return response objects
    with .isError() (bool) and .registers (a list of ints read, or an echo
    for writes).

    modbus-tk's modbus_tcp.TcpMaster instead exposes .open() for connecting,
    and a single .execute(slave_id, function_code, address, count=...)
    method that either returns a plain tuple of register values on success
    or raises a modbus_tk exception (subclass of modbus_tk.modbus.ModbusError,
    or a socket-level OSError for transport failures) on failure.

    This wrapper builds a small shim object that:
      * maps .connect() to master.open()
      * maps .read_holding_registers(address, count, unit=...) to
        master.execute(unit, cst.READ_HOLDING_REGISTERS, address, count),
        catching modbus-tk's exceptions and translating them into a result
        object with .isError() == True (mirroring how pymodbus reports a
        failed read via isError() rather than raising), and otherwise
        wrapping the returned tuple as result.registers (list) with
        isError() == False.
      * maps .write_register(address, value, unit=...) to
        master.execute(unit, cst.WRITE_SINGLE_REGISTER, address,
        output_value=value) with the same isError()/registers-style result
        shimming, so existing call sites (rr.isError(), rr.registers,
        wr.isError()) keep working unchanged.

    Exceptions that are not part of modbus-tk's reported failure modes are
    not caught here and propagate to the caller, matching the fact that
    pymodbus itself only converts protocol/transport failures into the
    isError() result -- it does not swallow arbitrary exceptions either.
    """
    import modbus_tk.modbus_tcp as modbus_tcp
    import modbus_tk.defines as cst
    from modbus_tk.modbus import ModbusError

    class _Result:
        def __init__(self, registers=None, error=False):
            self.registers = registers if registers is not None else []
            self._error = error

        def isError(self):
            return self._error

    class _TcpMasterShim:
        def __init__(self, host, port):
            self._master = modbus_tcp.TcpMaster(host=host, port=port)

        def connect(self):
            self._master.open()

        def read_holding_registers(self, address, count, unit=1):
            try:
                values = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except (ModbusError, OSError):
                return _Result(error=True)
            return _Result(registers=list(values), error=False)

        def write_register(self, address, value, unit=1):
            try:
                values = self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
            except (ModbusError, OSError):
                return _Result(error=True)
            return _Result(registers=list(values), error=False)

    return _TcpMasterShim(host, port)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_tcp_client_wrapper(host, port=port)
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
