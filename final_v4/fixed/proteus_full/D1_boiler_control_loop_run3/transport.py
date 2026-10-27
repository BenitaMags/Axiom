"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from modbustk_compat import build_modbustk_client


def _modbustk_client_compat_wrapper(host: str, port: int = 502):
    """Build a modbus-tk backed facade that mimics pymodbus's ModbusTcpClient.

    pymodbus's ``ModbusTcpClient`` exposes ``connect()``,
    ``read_holding_registers(address, count, unit=...)`` and
    ``write_register(address, value, unit=...)``, where the latter two return
    a response object with ``.registers``/``.isError()``. modbus-tk instead
    exposes a ``TcpMaster`` with ``open()`` and a single ``execute(slave_id,
    function_code, address, ...)`` call that returns a plain tuple (or raises
    on failure), and it names the slave/unit id positionally as ``slave_id``
    rather than as a ``unit=`` keyword. This wrapper translates between the
    two: it maps ``connect()`` -> ``open()``, maps the pymodbus ``unit=``
    kwarg to modbus-tk's positional ``slave_id`` argument, and converts
    modbus-tk's tuple-or-exception result into a ``_ModbusTkResult`` exposing
    ``.registers``/``.isError()`` like pymodbus did. Only modbus-tk's own
    protocol/response exceptions (``ModbusError``, including its
    ``ModbusInvalidResponseError`` subclass) are translated into an error
    result, matching the failure modes pymodbus reported via ``isError()``;
    anything else (e.g. a bad host) still propagates.
    """
    return build_modbustk_client(host, port)


def _default_client(host: str, port: int = 502):
    client = _modbustk_client_compat_wrapper(host, port)
    client.connect()
    return client


class ModbusIO:
    #: register scaling table: name -> (divisor, default address)
    _READ_SCALES = {
        "temperature": (10.0, 0x0000),
        "pressure": (100.0, 0x0001),
    }

    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def _read_scaled(self, kind: str, address: int) -> float:
        divisor, _ = self._READ_SCALES[kind]
        return self._read1(address) / divisor

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read_scaled("temperature", address)  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read_scaled("pressure", address)  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        clamped = max(0.0, min(100.0, percent))
        counts = int(round(clamped * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
