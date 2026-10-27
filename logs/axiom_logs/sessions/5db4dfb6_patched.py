"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""


def _axiom_modbus_tk_client_wrapper(host: str, port: int = 502):
    """Build a modbus-tk based client that mimics the pymodbus client API.

    pymodbus's `ModbusTcpClient.read_holding_registers(...)` and
    `.write_register(...)` return a response object exposing `.isError()`
    and, for reads, `.registers` (a list of ints). Failures are reported by
    `isError()` returning True rather than by raising.

    modbus-tk's `TcpMaster` has a totally different API: a single
    `execute(slave, function_code, address, count_or_kwargs)` call that
    either returns a tuple of register values or raises
    (`modbus_tk.exceptions.ModbusError`,
    `modbus_tk.exceptions.ModbusInvalidResponseError`, or a `socket.error`)
    on failure.

    This wrapper adapts modbus-tk's `TcpMaster` to expose the same shape the
    rest of this module already expects (`connect()`, `read_holding_registers
    (address, count, unit=...)`, `write_register(address, value, unit=...)`,
    each returning an object with `.isError()`/`.registers`), so call sites
    written against pymodbus's response contract keep working unchanged for
    the successful path. On failure, modbus-tk raises rather than returning a
    status; this wrapper does not swallow that exception into a fabricated
    `isError() == True` result -- it lets the exception propagate to the
    caller, exactly as it would if pymodbus itself had failed with a
    transport-level exception, so no failure is silently lost.
    """

    import socket

    import modbus_tk.defines as cst
    import modbus_tk.exceptions as mt_exceptions
    import modbus_tk.modbus_tcp as modbus_tcp

    class _Result:
        def __init__(self, registers=None, error=False):
            self.registers = registers
            self._error = error

        def isError(self):
            return self._error

    class _ModbusTkClientAdapter:
        def __init__(self, host, port):
            self._master = modbus_tcp.TcpMaster(host=host, port=port)
            self._master.set_timeout(5.0)

        def connect(self):
            self._master.open()

        def close(self):
            self._master.close()

        def read_holding_registers(self, address, count=1, unit=1):
            try:
                values = self._master.execute(
                    unit, cst.READ_HOLDING_REGISTERS, address, count
                )
            except (
                mt_exceptions.ModbusError,
                mt_exceptions.ModbusInvalidResponseError,
                socket.error,
            ):
                # pymodbus itself does not raise for a failed read/write; it
                # reports failure via isError(). However, swallowing the
                # exception here would hide the failure from any caller that
                # relies on the exception propagating (as it would have if
                # the underlying transport itself failed outside pymodbus's
                # own error-reporting path). Re-raise so the caller still
                # observes the failure instead of silently losing it.
                raise
            return _Result(registers=list(values), error=False)

        def write_register(self, address, value, unit=1):
            try:
                self._master.execute(
                    unit,
                    cst.WRITE_SINGLE_REGISTER,
                    address,
                    output_value=value,
                )
            except (
                mt_exceptions.ModbusError,
                mt_exceptions.ModbusInvalidResponseError,
                socket.error,
            ):
                # See read_holding_registers: re-raise rather than
                # converting to a return value, so the failure still reaches
                # the caller.
                raise
            return _Result(registers=None, error=False)

    return _ModbusTkClientAdapter(host, port)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbus_tk_client_wrapper(host, port=port)
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
