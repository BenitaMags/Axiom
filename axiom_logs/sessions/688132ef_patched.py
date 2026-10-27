"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""
import modbus_tk.exceptions as _mtk_exc


class _AxiomModbusResponse:
    """Lightweight stand-in for pymodbus's response objects.

    pymodbus's read/write calls return an object with `.isError()` and,
    for reads, `.registers` (a list of ints). modbus-tk's `TcpMaster.execute()`
    instead either returns a tuple of register values directly (on success)
    or raises a `modbus_tk.exceptions.ModbusError` (or a socket/transport
    error) on failure. This class re-packages either outcome into the
    pymodbus-shaped object so existing call sites (`rr.isError()`,
    `rr.registers[0]`) keep working unmodified.
    """

    def __init__(self, registers=None, error=None):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


class _AxiomModbusTkClientAdapter:
    """Adapts a modbus_tk TcpMaster to the pymodbus ModbusTcpClient surface
    used by this module (connect/close/read_holding_registers/write_register).
    """

    def __init__(self, master, cst):
        self._master = master
        self._cst = cst

    def connect(self):
        self._master.open()
        return True

    def close(self):
        self._master.close()

    def read_holding_registers(self, address, count, unit=1):
        try:
            result = self._master.execute(
                unit, self._cst.READ_HOLDING_REGISTERS, address, count
            )
        except Exception as exc:

            if isinstance(exc, (_mtk_exc.ModbusError, _mtk_exc.ModbusInvalidResponseError)):
                return _AxiomModbusResponse(error=exc)
            # Transport-level failures (socket errors, timeouts, etc.) are
            # also surfaced by pymodbus via .isError() rather than raised,
            # so translate anything modbus_tk raises here the same way.
            if isinstance(exc, OSError):
                return _AxiomModbusResponse(error=exc)
            raise
        return _AxiomModbusResponse(registers=result)

    def write_register(self, address, value, unit=1):
        try:
            self._master.execute(
                unit,
                self._cst.WRITE_SINGLE_REGISTER,
                address,
                output_value=value,
            )
        except Exception as exc:

            if isinstance(exc, (_mtk_exc.ModbusError, _mtk_exc.ModbusInvalidResponseError)):
                return _AxiomModbusResponse(error=exc)
            if isinstance(exc, OSError):
                return _AxiomModbusResponse(error=exc)
            raise
        return _AxiomModbusResponse()


def _axiom_modbus_tk_client_wrapper(host: str, port: int = 502):
    """Construct a modbus-tk TcpMaster and wrap it to look like a pymodbus
    ModbusTcpClient.

    pymodbus's `ModbusTcpClient(host, port=port)` gives callers `.connect()`,
    `.close()`, `.read_holding_registers(addr, count, unit=...)` and
    `.write_register(addr, value, unit=...)`, where the read/write calls
    return response objects exposing `.isError()`/`.registers`.

    modbus-tk instead exposes `modbus_tk.modbus_tcp.TcpMaster(host, port)`
    with `.open()`/`.close()` and a single `.execute(slave_id, function_code,
    address, count_or_kwargs)` method that returns a plain tuple of values
    on success and raises on failure. This wrapper builds the TcpMaster and
    returns an `_AxiomModbusTkClientAdapter` that re-exposes the pymodbus
    method names/signatures and re-packages modbus-tk's tuple-or-raise
    behavior into the isError()/registers response shape the call sites
    already expect.
    """
    import modbus_tk.defines as cst  # lazy import for testability
    import modbus_tk.modbus_tcp as modbus_tcp  # lazy import for testability

    master = modbus_tcp.TcpMaster(host=host, port=port)
    client = _AxiomModbusTkClientAdapter(master, cst)
    client.connect()
    return client


def _default_client(host: str, port: int = 502):
    return _axiom_modbus_tk_client_wrapper(host, port=port)


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
