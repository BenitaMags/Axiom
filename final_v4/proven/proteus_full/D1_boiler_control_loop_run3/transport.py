"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `modbus-tk` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from modbus_client_adapter import build_modbus_tk_client


def _axiom_modbus_client_adapter(host: str, port: int = 502):
    """Build a modbus-tk-backed client that mimics the pymodbus ModbusTcpClient
    surface area used by ModbusIO (connect/read_holding_registers/write_register).

    pymodbus's ``ModbusTcpClient.read_holding_registers`` /
    ``write_register`` return a response object exposing ``isError()`` (False
    on success, True if the slave replied with an exception code or the
    transport failed) and, for reads, a ``.registers`` list of the decoded
    16-bit values.

    modbus-tk has no such response object: ``modbus_tk.modbus_tcp.TcpMaster``
    is driven through a single ``execute(slave, function_code, address, ...)``
    call that either returns a tuple of register values (reads) or a tuple
    describing what was written (writes), and raises an exception -- a
    ``modbus_tk.exceptions.ModbusError`` (or one of its siblings such as
    ``ModbusInvalidResponseError``) for protocol-level failures, or a
    ``socket.error``/``socket.timeout`` for transport failures -- instead of
    returning a status.

    This adapter wraps a ``TcpMaster`` and re-exposes pymodbus's call
    signatures. It catches the modbus-tk/transport exceptions that pymodbus
    would have folded into ``isError() == True`` and converts them to a
    response object with ``isError()`` set, so ``ModbusIO`` can keep using
    its existing ``isError()`` / ``.registers[0]`` chain unmodified. No
    exception is swallowed silently: it is translated into the same signal
    pymodbus used for the same failure (a False/True error flag), never
    dropped.
    """
    return build_modbus_tk_client(host, port)


def _default_client(host: str, port: int = 502):

    client = _axiom_modbus_client_adapter(host, port=port)
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
