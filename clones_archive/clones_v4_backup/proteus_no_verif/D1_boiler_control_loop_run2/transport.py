"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's ``ModbusTcpClient`` call-site API onto modbus-tk.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()``/``close()`` plus
    ``read_holding_registers(address, count, unit=...)`` and
    ``write_register(address, value, unit=...)`` calls that return a
    response object with a ``.registers`` list and an ``.isError()`` method.

    modbus-tk instead uses a ``modbus_tcp.TcpMaster(host, port)`` object
    whose ``open()``/``close()`` manage the socket, and a single
    ``execute(slave_id, function_code, address, count/output_value=...)``
    call (using ``modbus_tk.defines`` function codes) that either returns
    the raw register value(s) as a tuple/list or raises a modbus_tk
    exception on failure -- there is no ``.registers``/``.isError()``
    response object and the slave/unit id is passed per-call rather than
    via a ``unit=`` keyword on a shared client.

    This wrapper constructs and opens a modbus_tk ``TcpMaster`` and returns
    a small shim object that keeps the old ``connect()``/``close()``,
    ``read_holding_registers(...)`` and ``write_register(...)`` call
    signatures used elsewhere in this module, translating each call into a
    modbus_tk ``execute(...)`` call, catching modbus_tk exceptions, and
    wrapping the outcome in a tiny response object that provides
    ``.registers`` (a list) and ``.isError()`` (a bool) so the rest of the
    file does not need to change its error-handling logic.
    """
    import modbus_tk.defines as cst
    from modbus_tk import modbus as modbus_tk_exceptions
    from modbus_tk import modbus_tcp

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.set_timeout(5.0)
    master.open()

    class _AxiomModbusResponse:
        def __init__(self, registers=None, error=False):
            self.registers = list(registers) if registers is not None else []
            self._error = error

        def isError(self) -> bool:
            return self._error

    class _AxiomModbusClientShim:
        def connect(self):
            # Connection is already opened by the wrapper; kept for API parity.
            return True

        def close(self):
            master.close()

        def read_holding_registers(self, address, count=1, unit=None):
            slave_id = unit if unit is not None else 1
            try:
                result = master.execute(
                    slave_id, cst.READ_HOLDING_REGISTERS, address, count
                )
                return _AxiomModbusResponse(registers=result, error=False)
            except (modbus_tk_exceptions.ModbusError, Exception):
                return _AxiomModbusResponse(registers=[], error=True)

        def write_register(self, address, value, unit=None):
            slave_id = unit if unit is not None else 1
            try:
                master.execute(
                    slave_id,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
                return _AxiomModbusResponse(registers=[value], error=False)
            except (modbus_tk_exceptions.ModbusError, Exception):
                return _AxiomModbusResponse(registers=[], error=True)

    return _AxiomModbusClientShim()


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_wrapper(host, port)
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
