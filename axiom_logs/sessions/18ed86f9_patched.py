"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `modbus-tk` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Build a pymodbus-compatible client adapter backed by modbus-tk.

    pymodbus's ``ModbusTcpClient`` exposes ``read_holding_registers(address,
    count, unit=...)`` and ``write_register(address, value, unit=...)``, each
    returning a response object with ``.isError()`` (a bool, no exception on
    a protocol-level failure) and, for reads, a ``.registers`` list of ints.

    modbus-tk instead exposes a master/query API: you build a
    ``modbus_tcp.TcpMaster``, open it once, and issue requests via
    ``master.execute(slave_id, function_code, address, count_or_kwargs)``.
    A successful read returns a plain tuple of register values; a successful
    single-register write returns a small tuple/int; and *failures raise an
    exception* (a subclass of ``modbus_tk.exceptions.ModbusError``) rather
    than returning a status flag.

    This wrapper opens one persistent ``TcpMaster`` connection and returns an
    adapter object that:
      * translates ``read_holding_registers``/``write_register`` calls into
        the equivalent ``master.execute(...)`` calls, and
      * converts the modbus-tk "raise on failure" model back into pymodbus's
        "return an object with .isError()" model, since that is exactly the
        contract pymodbus itself used for a protocol-level failure at this
        call site (the existing ``_read1``/``write_valve`` code below checks
        ``.isError()`` rather than expecting an exception). Only
        ``modbus_tk.exceptions.ModbusError`` (and subclasses) are caught for
        this translation -- any other exception (e.g. a programming error)
        still propagates unchanged.
    """
    from modbus_tk import modbus_tcp
    import modbus_tk.defines as cst
    import modbus_tk.exceptions as mt_exc

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.set_timeout(5.0)
    master.open()

    class _Response:
        def __init__(self, registers=None, error=False):
            self.registers = registers if registers is not None else []
            self._error = error

        def isError(self):
            return self._error

    class _ModbusTkClientAdapter:
        def __init__(self, master):
            self._master = master

        def read_holding_registers(self, address, count, unit=1):
            try:
                result = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except mt_exc.ModbusError:
                return _Response(error=True)
            return _Response(registers=list(result))

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
            except mt_exc.ModbusError:
                return _Response(error=True)
            return _Response(registers=[value])

        def connect(self):
            return True

        def close(self):
            self._master.close()

    return _ModbusTkClientAdapter(master)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_client_wrapper(host, port=port)
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
