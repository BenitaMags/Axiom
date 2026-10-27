"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from modbustk_bridge import build_modbustk_client


def _axiom_modbustk_client_wrapper(host: str, port: int = 502):
    """Bridge pymodbus's ModbusTcpClient API onto modbus-tk's Master/query API.

    pymodbus's ``ModbusTcpClient`` exposes ``read_holding_registers()`` and
    ``write_register()`` methods that return response objects with an
    ``.isError()`` predicate and, for reads, a ``.registers`` list; the
    client itself only raises in exceptional/transport-broken cases.

    modbus-tk instead exposes a ``Master``/query API: ``TcpMaster.execute()``
    takes a slave id and a function code, returns a plain tuple of register
    values on success (or an empty tuple for a successful single-register
    write), and *raises* a modbus-tk exception (a ``ModbusError`` subclass,
    or a transport-level ``OSError`` such as a connection refused/timeout)
    on failure instead of returning a flagged response.

    This wrapper constructs a modbus-tk ``TcpMaster`` and returns a small
    adapter object re-exposing ``connect()``, ``read_holding_registers()``
    and ``write_register()`` with pymodbus's response shape: it calls
    ``execute()``, catches modbus-tk's failure exceptions, and converts them
    into ``isError() == True`` responses (rather than letting them propagate
    as a different exception type), so ``ModbusIO``'s existing
    IOError-raising logic keeps working unmodified. Any other exception
    (i.e. a genuine programming error, not a communication failure) is left
    to propagate.
    """
    return build_modbustk_client(host, port=port)


def _default_client(host: str, port: int = 502):
    client = _axiom_modbustk_client_wrapper(host, port=port)
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
