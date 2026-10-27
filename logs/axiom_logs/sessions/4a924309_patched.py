

# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: pymodbus → modbus-tk
# The following functions bridge API incompatibilities between
# pymodbus and modbus-tk. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def create_modbus_tk_client(host: str, port: int = 502):
    """Wrapper factory replacing pymodbus.client.ModbusTcpClient(host, port=port).

    Original: pymodbus's ModbusTcpClient(host, port=port) returns a client object
    exposing .connect(), .read_holding_registers(address, count, unit=...) and
    .write_register(address, value, unit=...), where the read/write calls return
    a response object with .isError() and .registers.

    New: modbus_tk.modbus_tcp.TcpMaster(host=host, port=port) is a master object
    whose lifecycle is managed via .set_timeout()/.open() (no .connect()) and whose
    I/O is performed via .execute(unit, function_code, address, count_or_kwargs),
    which returns a plain tuple of register values (or raises
    modbus_tk.exceptions.ModbusError on failure) rather than a response object
    with .isError()/.registers.

    This wrapper constructs a modbus_tk TcpMaster and returns a shim object that
    re-exposes the pymodbus-shaped API (.connect(), .read_holding_registers(...),
    .write_register(...)) so that ModbusIO._read1 and write_valve keep working
    unchanged. Read/write results are wrapped into a small response object with
    .isError() (True if a ModbusError was raised during execute) and .registers
    (a list built from the tuple returned by execute; empty on error).

    Any exception other than modbus_tk.exceptions.ModbusError propagates
    unchanged, matching the original client's behavior of raising for
    connection/setup failures.
    """
    import modbus_tk.modbus_tcp as modbus_tcp
    import modbus_tk.defines as cst
    from modbus_tk.exceptions import ModbusError

    class _ModbusResponse:
        def __init__(self, registers=None, error=False):
            self._registers = registers or []
            self._error = error

        def isError(self):
            return self._error

        @property
        def registers(self):
            return self._registers

    class _ModbusTkClientShim:
        def __init__(self, host, port):
            self._master = modbus_tcp.TcpMaster(host=host, port=port)

        def connect(self):
            self._master.set_timeout(5.0)
            self._master.open()
            return True

        def read_holding_registers(self, address, count, unit=1):
            try:
                result = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except ModbusError:
                return _ModbusResponse(registers=[], error=True)
            return _ModbusResponse(registers=list(result), error=False)

        def write_register(self, address, value, unit=1):
            try:
                result = self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
            except ModbusError:
                return _ModbusResponse(registers=[], error=True)
            return _ModbusResponse(registers=list(result), error=False)

    return _ModbusTkClientShim(host, port)

"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _default_client(host: str, port: int = 502):

    client = create_modbus_tk_client(host, port=port)
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