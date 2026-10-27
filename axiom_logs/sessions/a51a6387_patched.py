"""transport -- Modbus field I/O for the boiler supervisor.

The Modbus client is one substitution target: `pymodbus` -> `minimalmodbus` (different
read/write API and error model -> glue required). The client is dependency-injected.
"""

import modbus_tk.defines as _mtk_defines
import modbus_tk.exceptions as _mtk_exceptions
import modbus_tk.modbus_tcp as _mtk_tcp


class _ModbusResponse:
    """Mimics a pymodbus response object.

    pymodbus's ``read_holding_registers``/``write_register`` calls return a
    response object exposing ``.isError()`` (bool) and, for reads,
    ``.registers`` (a list of ints); they do not raise on a Modbus-level
    failure. modbus-tk's ``TcpMaster.execute()`` instead returns the raw
    register tuple on success and *raises* a ``modbus_tk.exceptions.ModbusError``
    (or subclass) on any communication/protocol failure. This class
    reproduces the pymodbus response shape so call sites written against
    pymodbus (``_read1``/``write_valve``) keep working unmodified.
    """

    def __init__(self, registers=None, error=False):
        self.registers = list(registers) if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class _ModbusTkClientFacade:
    """Adapts modbus-tk's ``TcpMaster`` to the small subset of pymodbus's
    ``ModbusTcpClient`` API used by this module: ``connect()``,
    ``read_holding_registers(address, count, unit=)`` and
    ``write_register(address, value, unit=)``.

    pymodbus: ``client.connect()`` opens the TCP socket; the read/write
    calls return a response object with ``.isError()``/``.registers`` and
    never raise for a Modbus-level failure -- only for programming errors.

    modbus-tk: a ``TcpMaster(host, port)`` must be constructed and then
    ``.open()`` called to establish the connection; ``.execute(slave,
    function_code, address, quantity_of_x=..., output_value=...)`` performs
    the actual read/write and raises a ``modbus_tk.exceptions.ModbusError``
    (subclass) on any communication or protocol failure instead of
    returning an error flag.

    This facade opens the connection via ``connect()`` -> ``TcpMaster.open()``,
    and translates ``execute()`` results/exceptions into the pymodbus-style
    ``_ModbusResponse`` objects the rest of the module expects, reproducing
    pymodbus's own status-return behaviour for the same failure instead of
    inventing a new one.
    """

    def __init__(self, host, port=502):
        self._master = _mtk_tcp.TcpMaster(host=host, port=port, timeout_in_sec=5.0)

    def connect(self):
        self._master.open()
        return True

    def read_holding_registers(self, address, count=1, unit=1):
        try:
            result = self._master.execute(
                unit,
                _mtk_defines.READ_HOLDING_REGISTERS,
                address,
                count,
            )
        except _mtk_exceptions.ModbusError:
            return _ModbusResponse(error=True)
        return _ModbusResponse(registers=result)

    def write_register(self, address, value, unit=1):
        try:
            result = self._master.execute(
                unit,
                _mtk_defines.WRITE_SINGLE_REGISTER,
                address,
                output_value=value,
            )
        except _mtk_exceptions.ModbusError:
            return _ModbusResponse(error=True)
        return _ModbusResponse(registers=result)


def _axiom_modbus_client_wrapper(host, port=502):
    """Build and connect a modbus-tk-backed client that presents the same
    surface (.connect()/.read_holding_registers()/.write_register()) as
    pymodbus's ModbusTcpClient, so ModbusIO does not need to change. See
    _ModbusTkClientFacade for the detailed API translation.
    """
    client = _ModbusTkClientFacade(host, port=port)
    client.connect()
    return client


def _default_client(host: str, port: int = 502):

    # The connection is actually created via modbus-tk through
    # _axiom_modbus_client_wrapper; ModbusTcpClient above is unused.
    return _axiom_modbus_client_wrapper(host, port=port)


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
