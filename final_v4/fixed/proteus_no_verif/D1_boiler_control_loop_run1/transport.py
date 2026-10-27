"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

from modbus_shim import AxiomModbusClient, AxiomModbusResponse


def _default_client(host: str, port: int = 502):

    return _axiom_modbus_client_wrapper(host, port=port)


def _axiom_modbus_client_wrapper(host: str, port: int = 502):
    """Build a modbus-tk backed client that mimics the pymodbus client surface.

    pymodbus's ``ModbusTcpClient`` exposes ``.connect()`` plus
    ``.read_holding_registers(address, count, unit=...)`` and
    ``.write_register(address, value, unit=...)`` calls that each return a
    response *object* with ``.isError()`` and (for reads) ``.registers``.

    modbus-tk instead uses ``modbus_tcp.TcpMaster`` with a single
    ``execute(slave, function_code, address, ...)`` method that returns a
    raw tuple of register values on success and *raises* an exception on
    failure (protocol errors as ``modbus_tk.exceptions.ModbusError``
    subclasses, transport errors as socket errors) rather than encoding the
    failure in the return value.

    This wrapper constructs a ``TcpMaster`` and returns a small
    ``_AxiomModbusClient`` shim that re-exposes ``connect()``,
    ``read_holding_registers()`` and ``write_register()`` with the same
    signatures/semantics ``ModbusIO`` already relies on, translating
    modbus-tk exceptions into ``_AxiomModbusResponse`` objects that expose
    ``.isError()``/``.registers`` so the rest of this module needs no
    changes.
    """
    import modbus_tk.modbus_tcp as modbus_tcp  # lazy import for testability

    master = modbus_tcp.TcpMaster(host=host, port=port)
    master.set_timeout(5.0)
    client = _AxiomModbusClient(master)
    client.connect()
    return client


# Backwards-compatible aliases: the response/client shim now lives in
# modbus_shim, but keep the historical private names importable/usable from
# this module in case anything referenced them directly.
_AxiomModbusResponse = AxiomModbusResponse
_AxiomModbusClient = AxiomModbusClient


class ModbusIO:

    # Scaling table: address kwarg name -> (default address, divisor).
    # Keeps the two "read scaled register" methods as thin wrappers around
    # one generic implementation instead of near-duplicate bodies.
    _TEMPERATURE_ADDRESS = 0x0000
    _TEMPERATURE_DIVISOR = 10.0  # deci-degrees C -> C
    _PRESSURE_ADDRESS = 0x0001
    _PRESSURE_DIVISOR = 100.0  # centibar -> bar

    _VALVE_MIN_PERCENT = 0.0
    _VALVE_MAX_PERCENT = 100.0
    _VALVE_COUNTS_PER_PERCENT = 40.95  # 0-100% -> 0-4095

    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: str) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def _read_scaled(self, address: int, divisor: float) -> float:
        return self._read1(address) / divisor

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read_scaled(address, self._TEMPERATURE_DIVISOR)

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read_scaled(address, self._PRESSURE_DIVISOR)

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        clamped = max(self._VALVE_MIN_PERCENT, min(self._VALVE_MAX_PERCENT, percent))
        counts = int(round(clamped * self._VALVE_COUNTS_PER_PERCENT))
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
